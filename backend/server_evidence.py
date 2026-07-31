"""M0 最小后端：POST /a2a -> 循证卡的 A2UI 消息。

链路：mock skill(读 fixture) -> EvidenceAnswer -> evidence_to_a2ui -> A2UI parts。
M0 忽略前端传来的问题，恒返回氯雷他定卡（跑通端到端优先）。
M2 再把 mock 换成真实 medical-pico-search skill + LLM 组织 EvidenceAnswer。

跑：cd backend && uv run python server_evidence.py   (默认 :8700)
"""
from __future__ import annotations

import json
import pathlib

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.evidence import EvidenceAnswer, evidence_to_a2ui

FIXTURE = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "loratadine.json"

app = FastAPI(title="evidence-a2ui M0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


def _mock_retrieve(question: str) -> EvidenceAnswer:
    # ponytail: M0 恒返回 fixture；真实检索(medical-pico-search)留给 M2
    return EvidenceAnswer.model_validate(json.loads(FIXTURE.read_text("utf-8")))


@app.post("/a2a")
async def a2a(request: Request):
    question = (await request.body()).decode("utf-8").strip()
    answer = _mock_retrieve(question)
    parts = [{"kind": "text", "text": answer.intro}]
    parts += [{"kind": "data", "data": m} for m in evidence_to_a2ui(answer)]
    return parts


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8700)
