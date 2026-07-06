"""企业 Agent 自定义工具。

工具：
- todo:           任务规划/进度跟踪，状态存于 tool_context.state（单次会话内有效）
- terminal:       在服务器上执行 shell 命令（高权限，请在受信部署边界内使用）
- vision_analyze: 图片内容分析 / OCR（单独的视觉模型）
- clarify:        人在回路澄清提问（LongRunningFunctionTool，需用户回答后续跑）

注册方式见 app/agent.py：普通函数用 FunctionTool 包装，clarify 直接用
已包装好的 clarify_tool 加入 tools=[...]。
"""

from __future__ import annotations

import base64
import os
import pathlib
import subprocess

from google.adk.tools import LongRunningFunctionTool, ToolContext

# 上传目录（与 agent.py 一致；此处本地定义以避免循环导入）
UPLOADS_DIR = pathlib.Path(__file__).parent.parent / "uploads"

# ---------------------------------------------------------------------------
# todo — 任务规划
# ---------------------------------------------------------------------------
_TODO_KEY = "_todo_list"
_VALID_STATUS = {"pending", "in_progress", "completed", "cancelled"}
_MAX_ITEMS = 256
_MAX_CONTENT = 4000


def todo(tool_context: ToolContext, todos: list[dict] | None = None) -> dict:
    """管理本次任务的待办清单，用于拆解复杂任务并跟踪进度。

    用法：
    - 传入 todos 写入/覆盖整张清单；不传则只读取当前清单。
    - 每个待办项为 {"id": str, "content": str, "status": "..."}，
      status 取值：pending / in_progress / completed / cancelled。
    - 列表顺序即优先级。复杂任务（>=3 步）开始时先写清单，
      每完成一步立即把对应项更新为 completed，并把下一项设为 in_progress。

    返回当前完整清单 {"todos": [...]}。
    """
    items = list(tool_context.state.get(_TODO_KEY, []))
    if todos is not None:
        cleaned: list[dict] = []
        for t in todos[:_MAX_ITEMS]:
            if not isinstance(t, dict):
                continue
            status = str(t.get("status", "pending")).strip().lower()
            cleaned.append({
                "id": str(t.get("id", "")).strip() or str(len(cleaned) + 1),
                "content": str(t.get("content", "")).strip()[:_MAX_CONTENT],
                "status": status if status in _VALID_STATUS else "pending",
            })
        items = cleaned
        tool_context.state[_TODO_KEY] = items
    return {"todos": items}


# ---------------------------------------------------------------------------
# terminal — shell 命令执行
# ---------------------------------------------------------------------------
_TERMINAL_MAX_STDOUT = 8000
_TERMINAL_MAX_STDERR = 2000


async def terminal(command: str, tool_context: ToolContext, timeout: int = 60) -> dict:
    """执行 shell 命令并返回输出。

    适用于查看文件、运行构建/检索脚本、查询系统信息等。
    命令在当前租户的隔离沙箱中执行（多租户互不干扰）；未启用沙箱时在服务器本地执行。

    参数：
    - command: 要执行的 shell 命令。
    - timeout: 超时秒数（默认 60）。

    返回 {"stdout", "stderr", "returncode"}；超时返回 {"error"}。
    """
    if not command or not command.strip():
        return {"error": "command 不能为空"}

    from app import sandbox as sbx

    if sbx.enabled():
        # 租户身份即隔离边界；server 在 create_session 时把 user_id 写入 _sbkey。
        key = str(tool_context.state.get("_sbkey") or tool_context.state.get("user_id") or "default")
        try:
            return await sbx.run_async(key, command, timeout)
        except Exception as e:  # noqa: BLE001 — 工具边界，统一回报给模型
            return {"error": f"沙箱执行失败: {e}"}

    try:
        r = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "stdout": r.stdout[-_TERMINAL_MAX_STDOUT:],
            "stderr": r.stderr[-_TERMINAL_MAX_STDERR:],
            "returncode": r.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"error": f"命令超时（>{timeout}s）"}
    except Exception as e:  # noqa: BLE001 — 工具边界，统一回报给模型
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# vision_analyze — 图片内容分析 / OCR
# ---------------------------------------------------------------------------
# DeepSeek 无视觉能力，这里单独用一个视觉模型（默认 qwen-vl-max，OpenAI 兼容端点）。
_VISION_MODEL = os.environ.get("VISION_MODEL", "openai/qwen-vl-max")
_VISION_KEY = os.environ.get("VISION_API_KEY")
_VISION_BASE = os.environ.get(
    "VISION_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1"
)
_VISION_MAX_BYTES = 10 * 1024 * 1024  # 单图 10MB 上限
_IMAGE_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
}


def vision_analyze(image: str, prompt: str = "描述这张图片的内容，并识别其中的文字") -> dict:
    """分析图片内容或对图片做 OCR 文字识别。

    适用于用户上传了图片（如截图、图表、扫描件、含文字的图片）需要理解或提取文字的场景。
    image 可以是 http(s) 图片 URL，或用户已上传的图片文件名（位于 uploads 目录）。
    prompt 描述你想从图片中获取什么信息（如"提取图中表格数据"、"这是什么模型结构图"）。

    返回 {"analysis": str}，失败返回 {"error": str}。
    """
    if not _VISION_KEY:
        return {"error": "未配置 VISION_API_KEY，无法使用图片分析。请在 .env 中设置视觉模型。"}

    try:
        if image.startswith(("http://", "https://")):
            import httpx
            resp = httpx.get(image, timeout=30, follow_redirects=True)
            resp.raise_for_status()
            data = resp.content
            mime = resp.headers.get("content-type", "image/jpeg").split(";")[0]
            if not mime.startswith("image/"):
                mime = "image/jpeg"
        else:
            p = next(UPLOADS_DIR.rglob(image), None)
            if p is None or not p.is_file():
                return {"error": f"未找到图片文件: {image}（请确认已上传）"}
            data = p.read_bytes()
            mime = _IMAGE_MIME.get(p.suffix.lower(), "image/jpeg")

        if len(data) > _VISION_MAX_BYTES:
            return {"error": f"图片过大（{len(data) // 1024 // 1024}MB > 10MB），请压缩后重试。"}

        b64 = base64.b64encode(data).decode()
        import litellm
        completion = litellm.completion(
            model=_VISION_MODEL,
            api_key=_VISION_KEY,
            api_base=_VISION_BASE,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ],
            }],
        )
        return {"analysis": completion.choices[0].message.content}
    except Exception as e:  # noqa: BLE001 — 工具边界，统一回报给模型
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# clarify — 人在回路澄清提问
# ---------------------------------------------------------------------------
def clarify(question: str, choices: list[str] | None = None) -> dict:
    """当用户需求不明确、存在多种可能理解，或缺少关键信息无法继续时，向用户提问澄清。

    用法：
    - question: 你想向用户确认的问题，应具体、简洁。
    - choices: 可选项列表（最多 4 项）。提供选项能让用户更快回答；
      若是开放式问题可不传。

    这是一个长时运行工具：调用后会暂停并等待用户回答，用户答复后你将
    收到答案并据此继续。只在确有必要时使用，不要为已经清楚的需求反复发问。
    """
    return {
        "status": "pending",
        "question": question,
        "choices": list(choices or [])[:4],
    }


# 包装为长时运行工具：调用后挂起，等待用户通过 /chat/answer 回灌答复。
clarify_tool = LongRunningFunctionTool(func=clarify)
