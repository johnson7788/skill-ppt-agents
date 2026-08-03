"""A2A AgentExecutor：把 A2A 请求喂给 EvidenceAgent，按 context_id 维持多轮会话。

- context_id 即 session_id = InMemorySession 的 key = 真·多轮状态。
- 用户输入优先取文本；若是 A2UI 组件回传的 action（DataPart），翻译成自然语言 query。
"""
from __future__ import annotations

import logging

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import DataPart, Part, Task, TaskState, UnsupportedOperationError
from a2a.utils import new_agent_parts_message, new_task
from a2a.utils.errors import ServerError

from .agent import EvidenceAgent

logger = logging.getLogger(__name__)


def _extract_query(context: RequestContext) -> str:
    """从消息里取 query：优先 A2UI action（v0.9 {action} / v0.8 {userAction}），否则文本。"""
    if context.message and context.message.parts:
        for part in context.message.parts:
            if isinstance(part.root, DataPart):
                data = part.root.data
                action = data.get("action") or data.get("userAction")
                if action:
                    name = action.get("name", "")
                    ctx = action.get("context", {})
                    return f"用户在卡片上触发了操作：{name}，参数：{ctx}"
    return context.get_user_input()


class EvidenceAgentExecutor(AgentExecutor):
    def __init__(self, agent: EvidenceAgent):
        self._agent = agent

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        query = _extract_query(context)
        logger.info("--- query: %s ---", query)

        task = context.current_task
        if not task:
            task = new_task(context.message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)

        async for parts in self._agent.stream(query, task.context_id):
            await updater.update_status(
                TaskState.input_required,
                new_agent_parts_message(parts, task.context_id, task.id),
                final=True,
            )

    async def cancel(
        self, request: RequestContext, event_queue: EventQueue
    ) -> Task | None:
        raise ServerError(error=UnsupportedOperationError())
