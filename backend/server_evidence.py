"""evidence-a2ui 后端：POST /a2a -> 循证卡的 A2UI 消息。

M2 链路：用户问题 -> PICO 抽取 -> medical-pico-search 检索 -> LLM 组织
EvidenceAnswer -> evidence_to_a2ui -> A2UI parts。真实证据、版式不变。

失败兜底：任一步异常则返回纯文字提示 part（不渲染卡），前端照常显示。
设 EVIDENCE_MOCK=1 时走 fixture（离线联调用，不触网）。

跑：cd backend && uv run python server_evidence.py   (默认 :8700)
"""
from __future__ import annotations

import json
import os
import pathlib

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.evidence import EvidenceAnswer, evidence_to_a2ui
from app.evidence.pipeline import answer_question

FIXTURE = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "loratadine.json"

app = FastAPI(title="evidence-a2ui")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


async def _retrieve(question: str) -> EvidenceAnswer:
    if os.environ.get("EVIDENCE_MOCK") == "1":
        return EvidenceAnswer.model_validate(json.loads(FIXTURE.read_text("utf-8")))
    return await answer_question(question)


@app.post("/a2a")
async def a2a(request: Request):
    question = (await request.body()).decode("utf-8").strip()
    try:
        answer = await _retrieve(question)
    except Exception as e:  # noqa: BLE001 — 边界兜底，任何失败都给用户友好提示
        return [{"kind": "text", "text": f"抱歉，暂时无法完成循证分析：{e}"}]
    parts = [{"kind": "text", "text": answer.intro}]
    parts += [{"kind": "data", "data": m} for m in evidence_to_a2ui(answer)]
    return parts


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8700)
