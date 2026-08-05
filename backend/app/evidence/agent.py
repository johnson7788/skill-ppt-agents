"""EvidenceAgent：ADK LlmAgent(DeepSeek via LiteLlm) + A2A + InMemorySession。

B-Hybrid（见 doc/回答sop.md）：官方传输 + 确定性渲染。
- 单个 LlmAgent 用工具做路由：临床循证问题→search_evidence，自测量表→make_questionnaire，
  闲聊→不调工具、以「小团健康管家」人设直接文本回复。
- 工具不手搓 A2UI，只把结构化 EvidenceAnswer/Questionnaire（model_dump 后的 dict）压进
  tool_context.state['render_queue']，返回一句 ack。stream() 跑完后按 per-session 水位线
  drain，经确定性 mapper 出合法 A2UI 消息 → A2A DataPart。
- InMemorySessionService 按 A2A context_id 存全量多轮历史 = 真·有状态。

多轮状态天然由 ADK session 承担：同一 session_id 复用历史，agent 看得到上文再路由。
"""
from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterable
from typing import Any

from google.adk.agents.llm_agent import LlmAgent
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.models.lite_llm import LiteLlm
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from a2a.types import Part, TextPart
from a2ui.a2a.parts import create_a2ui_part
from a2ui.schema.constants import VERSION_0_9

from . import mapper
from .pipeline import build_answer, build_questionnaire, run_search
from .schema import EvidenceAnswer, Questionnaire

_INSTRUCTION = (
    "你是「小团健康管家」，一位温暖、耐心、有同理心的 AI 健康助手，同时也是循证医学助手。"
    "根据用户每条消息（结合多轮上下文）选择处理方式：\n"
    "1) 临床循证问题（问某疾病/症状能否用某药、某治疗是否有效等需文献支撑的问题）："
    "抽取 PICO 四要素后调用 search_evidence（question 传用户原问题，P=人群/疾病、I=干预/药物、"
    "C=对照可空、O=结局可空；关键词供向量检索、禁含年份）。调用后只回一句简短引导语，"
    "不要复述工具内容（循证卡片会单独渲染）。\n"
    "2) 自测/自评一个可量表化的症状群（如「测测我是不是抑郁」「焦虑严重吗」「评估睡眠质量」）："
    "调用 make_questionnaire（symptom 传症状描述）。调用后只回一句引导语。\n"
    "3) 寒暄/致谢/闲聊/与医疗无关/软性健康关怀：不调用任何工具，直接以温暖口语的中文回应，"
    "先共情再给中肯建议或轻轻追问，2-4 句，别长篇，涉及健康问题时温和提示必要时就医。"
)


async def search_evidence(
    question: str,
    P: str,
    I: str,
    tool_context: ToolContext,
    C: str = "",
    O: str = "",
) -> str:
    """检索文献并生成循证结论卡片。question=用户原问题；P/I/C/O=PICO 四要素关键词。"""
    pico = {"P": P, "I": I, "C": C or "", "O": O or ""}
    results = await run_search(pico)
    answer = await build_answer(question, pico, results)
    queue = tool_context.state.get("render_queue", [])
    tool_context.state["render_queue"] = queue + [
        {"type": "evidence", "payload": answer.model_dump()}
    ]
    return "已生成循证卡片"


async def make_questionnaire(symptom: str, tool_context: ToolContext) -> str:
    """匹配并生成一份自评量表。symptom=用户想自测的症状描述。"""
    q = await build_questionnaire(symptom)
    queue = tool_context.state.get("render_queue", [])
    tool_context.state["render_queue"] = queue + [
        {"type": "questionnaire", "payload": q.model_dump()}
    ]
    return "已生成自评量表"


def _render_parts(item: dict[str, Any]) -> list[Part]:
    """把一条 drain 出来的渲染项 → A2UI DataParts（每张卡片独立 surfaceId 防覆盖）。"""
    sid = f"card-{uuid.uuid4().hex[:8]}"
    if item["type"] == "evidence":
        answer = EvidenceAnswer.model_validate(item["payload"])
        msgs = mapper.evidence_to_a2ui(answer, surface_id=sid)
    else:
        q = Questionnaire.model_validate(item["payload"])
        msgs = [
            mapper.create_surface_msg(sid),
            mapper.update_components_msg(mapper.questionnaire_components(q), sid),
        ]
    return [create_a2ui_part(m, version=VERSION_0_9) for m in msgs]


# 工具调用旁白：检索/生成阶段耗时长且 ADK 在工具执行期间不产事件，
# 一检测到 function_call 就先发一句进度文本，让前端先看到动的反馈。
_TOOL_PROGRESS = {
    "search_evidence": "正在检索指南 / Meta 分析 / RCT，稍等…\n\n",
    "make_questionnaire": "正在为你匹配自评量表…\n\n",
}
# 工具返回旁白：function_response 事件一到就发一句，标明「检索结束、进入生成」。
_TOOL_RESPONSE = {
    "search_evidence": "已检索到候选文献，正在综合生成循证结论…\n\n",
    "make_questionnaire": "已匹配到自评量表…\n\n",
}


def _stream_texts(ev) -> tuple[list[str], list[str]]:
    """返回 (正文片段, 思考片段)。思考链单独通道：逐块转发给前端做「思考」气泡。"""
    texts: list[str] = []
    thoughts: list[str] = []
    if ev.content and ev.content.parts:
        for p in ev.content.parts:
            if not p.text:
                continue
            if getattr(p, "thought", False):
                thoughts.append(p.text)
            else:
                texts.append(p.text)
    return texts, thoughts


class EvidenceAgent:
    """循证问答 A2UI 智能体。"""

    def __init__(self) -> None:
        prov = os.getenv("MODEL_PROVIDER", "deepseek")
        name = os.getenv("MODEL_NAME", "deepseek-v4-pro")
        key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("MODEL_API_KEY")
        base = os.getenv("DEEPSEEK_BASE_URL") or os.getenv("MODEL_BASE_URL")
        llm = LiteLlm(model=f"{prov}/{name}", api_key=key, api_base=base)
        self._app_name = "evidence-a2ui"
        self._user_id = "web"
        self._svc = InMemorySessionService()
        self._runner = Runner(
            app_name=self._app_name,
            agent=LlmAgent(
                model=llm,
                name="evidence_agent",
                instruction=_INSTRUCTION,
                tools=[search_evidence, make_questionnaire],
            ),
            session_service=self._svc,
            artifact_service=None,
            memory_service=None,
        )
        self._marks: dict[str, int] = {}  # per-session render_queue 水位线（drain 去重）

    async def stream(self, query: str, session_id: str) -> AsyncIterable[list[Part]]:
        """跑一轮，**逐帧**产出这一轮的 A2A Parts（真·流式）。

        开 `RunConfig(streaming_mode=SSE)`：ADK 把 LLM 正文按 token 增量
        （`partial=True` 事件）逐块抛出，每块立刻 yield 一个 TextPart 帧，前端逐字见字。
        注意 ADK 在一段流式响应结束后还会再抛一条「整段全文」的完整事件（与增量重复），
        需跳过、否则前端累加会翻倍；模型不流式（只有一条完整事件）时照常兜底下发。
        卡片不随文本流：render_queue 要等工具跑完才有内容，故在整轮结束后按水位线
        drain，作为最后一帧（createSurface + updateComponents 的 DataParts）下发。
        """
        sess = await self._svc.get_session(
            app_name=self._app_name, user_id=self._user_id, session_id=session_id
        )
        if sess is None:
            sess = await self._svc.create_session(
                app_name=self._app_name, user_id=self._user_id, session_id=session_id
            )
        mark = self._marks.get(session_id, 0)

        msg = types.Content(role="user", parts=[types.Part.from_text(text=query)])
        run_config = RunConfig(streaming_mode=StreamingMode.SSE)

        streamed_segment = False  # 当前这段 LLM 响应是否已流过增量（用于跳过结尾的整段重复事件）
        streamed_thought = False
        progress_done: set[tuple[str, str]] = set()  # 每个工具进度旁白只发一次（partial+完整事件会重复）
        async for ev in self._runner.run_async(
            user_id=self._user_id,
            session_id=session_id,
            new_message=msg,
            run_config=run_config,
        ):
            if ev.content and ev.content.parts:
                # 工具调用/返回事件：发进度旁白（检索阶段不再白屏）
                for p in ev.content.parts:
                    fc = getattr(p, "function_call", None)
                    if fc is not None and ("call", fc.name) not in progress_done:
                        progress_done.add(("call", fc.name))
                        hint = _TOOL_PROGRESS.get(fc.name)
                        if hint:
                            yield [Part(root=TextPart(text=hint))]
                    fr = getattr(p, "function_response", None)
                    fr_name = getattr(fr, "name", "")
                    if fr is not None and ("resp", fr_name) not in progress_done:
                        progress_done.add(("resp", fr_name))
                        hint = _TOOL_RESPONSE.get(fr_name)
                        if hint:
                            yield [Part(root=TextPart(text=hint))]
                texts, thoughts = _stream_texts(ev)
                if ev.partial:
                    # token 增量：思考链与正文分通道逐块下发
                    for t in thoughts:
                        streamed_thought = True
                        yield [Part(root=TextPart(text=t, metadata={"thinking": True}))]
                    for t in texts:
                        streamed_segment = True
                        yield [Part(root=TextPart(text=t))]
                else:
                    # 非增量事件：若是流式段结尾的「整段全文」则与已发增量重复 → 跳过；
                    # 若模型压根没流式（无增量）→ 兜底下发整段。
                    thought = "".join(thoughts)
                    if thought and not streamed_thought:
                        yield [Part(root=TextPart(text=thought, metadata={"thinking": True}))]
                    text = "".join(texts)
                    if text and not streamed_segment:
                        yield [Part(root=TextPart(text=text))]
                    streamed_segment = False
                    streamed_thought = False

        # 卡片帧：drain 本轮新增的 render_queue → 确定性 mapper → A2UI DataParts
        sess = await self._svc.get_session(
            app_name=self._app_name, user_id=self._user_id, session_id=session_id
        )
        queue = sess.state.get("render_queue", [])
        card_parts: list[Part] = []
        for item in queue[mark:]:
            card_parts.extend(_render_parts(item))
        self._marks[session_id] = len(queue)
        if card_parts:
            yield card_parts


def _selfcheck() -> None:
    """自检：`python -m app.evidence.agent`。喂 fixtures 验渲染路径（不触网/LLM）。"""
    import json
    import pathlib

    from a2a.types import DataPart

    F = pathlib.Path(__file__).resolve().parents[3] / "fixtures"
    ev = _render_parts(
        {"type": "evidence", "payload": json.loads((F / "loratadine.json").read_text("utf-8"))}
    )
    qz = _render_parts(
        {"type": "questionnaire", "payload": json.loads((F / "phq9.json").read_text("utf-8"))}
    )
    assert len(ev) == 2 and isinstance(ev[0].root, DataPart)
    assert "a2ui" in ev[0].root.metadata["mimeType"]
    ev_comps = ev[1].root.data["updateComponents"]["components"]
    assert any(c["id"] == "root" and c["component"] == "Card" for c in ev_comps)
    qz_comps = qz[1].root.data["updateComponents"]["components"]
    assert qz_comps[0]["id"] == "root" and qz_comps[0]["component"] == "Questionnaire"
    # 每张卡片独立 surfaceId，避免同会话内互相覆盖
    assert (
        ev[0].root.data["createSurface"]["surfaceId"]
        != qz[0].root.data["createSurface"]["surfaceId"]
    )
    print("agent selfcheck OK")


if __name__ == "__main__":
    _selfcheck()
