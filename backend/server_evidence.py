"""evidence-a2ui 后端：POST /a2a -> 循证卡的 A2UI 消息。

M2 链路：用户问题 -> PICO 抽取 -> medical-pico-search 检索 -> LLM 组织
EvidenceAnswer -> mapper -> A2UI parts。真实证据、版式不变。

M3 增量更新：按 session_id 记住上一轮组件。首轮返回 createSurface + 全量
updateComponents；追问只返回**差异组件**的 updateComponents（同 surfaceId，
按 id 原地合并），卡片不重建、不整块重画。history 喂回 pipeline 让结论连续。

失败兜底：任一步异常则返回纯文字提示 part。EVIDENCE_MOCK=1 走 fixture 离线联调。

跑：cd backend && uv run python server_evidence.py   (默认 :8700)
"""
from __future__ import annotations

import json
import os
import pathlib

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.evidence import EvidenceAnswer, evidence_to_a2ui  # noqa: F401 (兼容导出)
from app.evidence.mapper import (
    create_surface_msg,
    evidence_components,
    update_components_msg,
)
from app.evidence.pipeline import answer_question

FIXTURE = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "loratadine.json"

app = FastAPI(title="evidence-a2ui")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

# 进程内会话态：session_id -> {"comps": 上轮组件, "questions": 追问历史}
# ponytail: 进程内 dict，重启即清；前端每次加载生成新 session_id 故首轮总带 createSurface。
_SESSIONS: dict[str, dict] = {}


async def _retrieve(question: str, history: list[str]) -> EvidenceAnswer:
    if os.environ.get("EVIDENCE_MOCK") == "1":
        return EvidenceAnswer.model_validate(json.loads(FIXTURE.read_text("utf-8")))
    return await answer_question(question, history)


@app.post("/a2a")
async def a2a(request: Request):
    data = await request.json()
    question = str(data.get("question", "")).strip()
    sid = str(data.get("session_id") or "default")
    sess = _SESSIONS.get(sid)
    history: list[str] = sess["questions"] if sess else []

    try:
        answer = await _retrieve(question, history)
    except Exception as e:  # noqa: BLE001 — 边界兜底，任何失败都给用户友好提示
        return [{"kind": "text", "text": f"抱歉，暂时无法完成循证分析：{e}"}]

    comps = evidence_components(answer)
    parts: list[dict] = [{"kind": "text", "text": answer.intro}]
    if sess is None:
        # 首轮：建 surface + 全量组件
        parts.append({"kind": "data", "data": create_surface_msg()})
        parts.append({"kind": "data", "data": update_components_msg(comps)})
    else:
        # 追问：只发差异组件（含因子/子节点变化的 root_col），surface 不重建
        prev = {c["id"]: c for c in sess["comps"]}
        diff = [c for c in comps if prev.get(c["id"]) != c]
        parts.append({"kind": "data", "data": update_components_msg(diff)})

    _SESSIONS[sid] = {"comps": comps, "questions": history + [question]}
    return parts


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8700)
