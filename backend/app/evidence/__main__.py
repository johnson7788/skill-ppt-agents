"""循证 A2UI 智能体 A2A 服务入口（:8700）。

启动：cd backend && .venv/bin/python -m app.evidence  （或 --port 覆盖）
"""
from __future__ import annotations

import logging

import click
import uvicorn
from dotenv import load_dotenv
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from a2ui.a2a.extension import get_a2ui_agent_extension
from a2ui.schema.constants import VERSION_0_9
from starlette.middleware.cors import CORSMiddleware

from .agent import EvidenceAgent
from .agent_executor import EvidenceAgentExecutor
from .mapper import CATALOG_ID

load_dotenv()
logging.basicConfig(level=logging.INFO)


def _agent_card(base_url: str) -> AgentCard:
    ext = get_a2ui_agent_extension(
        VERSION_0_9, accepts_inline_catalogs=False, supported_catalog_ids=[CATALOG_ID]
    )
    return AgentCard(
        name="循证问答智能体",
        description="循证医学问答 + 自评量表 + 健康闲聊，输出 A2UI 声明式界面。",
        url=base_url,
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(streaming=True, extensions=[ext]),
        skills=[
            AgentSkill(
                id="evidence_qa",
                name="循证问答",
                description="基于文献检索给出带证据等级与引用的循证结论卡片。",
                tags=["medical", "evidence", "a2ui"],
                examples=["高血压患者能用氨氯地平吗？", "测测我是不是抑郁"],
            )
        ],
    )


@click.command()
@click.option("--host", default="localhost")
@click.option("--port", default=8700)
def main(host: str, port: int) -> None:
    base_url = f"http://{host}:{port}"
    agent = EvidenceAgent()
    handler = DefaultRequestHandler(
        agent_executor=EvidenceAgentExecutor(agent),
        task_store=InMemoryTaskStore(),
    )
    server = A2AStarletteApplication(
        agent_card=_agent_card(base_url), http_handler=handler
    )
    app = server.build()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
    )
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
