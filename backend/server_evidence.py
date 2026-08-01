"""evidence-a2ui 后端：POST /a2a -> 循证卡的 A2UI 消息。

M2 链路：用户问题 -> PICO 抽取 -> medical-pico-search 检索 -> LLM 组织
EvidenceAnswer -> mapper -> A2UI parts。真实证据、版式不变。

多轮对话：每轮一张独立卡片（surfaceId 由前端每轮生成、唯一），始终
createSurface + 全量 updateComponents。追问的连续性靠前端回传 history
（此前各轮的问题）喂回 pipeline，后端无会话态。

失败兜底：任一步异常则返回纯文字提示 part。EVIDENCE_MOCK=1 走 fixture 离线联调。

跑：cd backend && uv run python server_evidence.py   (默认 :8700)
"""
from __future__ import annotations

import json
import os
import pathlib

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app.evidence import EvidenceAnswer, evidence_to_a2ui  # noqa: F401 (兼容导出)
from app.evidence.mapper import (
    SURFACE_ID,
    create_surface_msg,
    evidence_components,
    update_components_msg,
)
from app.evidence.pipeline import stream_answer

FIXTURE = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "loratadine.json"

app = FastAPI(title="evidence-a2ui")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

@app.get("/")
async def health():
    return {"ok": True}


def _sse(evt: dict) -> str:
    return f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"


async def _retrieve_stream(question: str, history: list[str]):
    """产 pipeline 事件：{"kind":"status"/"thinking"/"answer"}。
    EVIDENCE_MOCK=1 时不触网，发两条 status 旁白后直接给 fixture answer。"""
    if os.environ.get("EVIDENCE_MOCK") == "1":
        yield {"kind": "status", "text": "抽取临床要素（PICO）…"}
        yield {"kind": "status", "text": "检索指南 / Meta 分析 / RCT…"}
        yield {"kind": "status", "text": "综合证据、生成循证结论…"}
        ans = EvidenceAnswer.model_validate(json.loads(FIXTURE.read_text("utf-8")))
        yield {"kind": "answer", "answer": ans}
        return
    async for evt in stream_answer(question, history):
        yield evt


async def _a2a_events(question: str, surface_id: str, history: list[str]):
    """把 pipeline 事件流映射成 SSE 事件流（见 doc/stream_plan.md §1）。

    每轮一张独立卡片：始终 createSurface + 全量组件（surfaceId 由前端每轮生成，唯一）。
    追问上下文 history 由前端回传（此前各轮的问题），后端无会话态。
    """
    try:
        async for evt in _retrieve_stream(question, history):
            kind = evt["kind"]
            if kind == "status":
                yield _sse({"kind": "status", "text": evt["text"]})
            elif kind == "thinking":
                yield _sse({"kind": "thinking", "delta": evt["delta"]})
            elif kind == "chat":
                # 闲聊/致谢：纯文字回复，不建 surface、不出循证卡
                yield _sse({"kind": "text", "text": evt["text"]})
            elif kind == "answer":
                answer: EvidenceAnswer = evt["answer"]
                comps = evidence_components(answer)
                yield _sse({"kind": "text", "text": answer.intro})
                yield _sse({"kind": "data", "data": create_surface_msg(surface_id)})
                yield _sse({"kind": "data", "data": update_components_msg(comps, surface_id)})
    except Exception as e:  # noqa: BLE001 — 边界兜底，任何失败都给友好提示，不留死流
        yield _sse({"kind": "error", "text": f"抱歉，暂时无法完成循证分析：{e}"})
    yield _sse({"kind": "done"})


@app.post("/a2a")
async def a2a(request: Request):
    data = await request.json()
    question = str(data.get("question", "")).strip()
    surface_id = str(data.get("surface_id") or SURFACE_ID)
    history = [str(h) for h in data.get("history", [])]
    return StreamingResponse(
        _a2a_events(question, surface_id, history), media_type="text/event-stream"
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8700)
