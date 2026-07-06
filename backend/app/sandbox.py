"""按租户（user_id）隔离的命令执行 —— 基于 OpenSandbox。

每个租户分配一个独占沙箱：文件系统、进程、已装的包互不干扰，实现安全的多租户隔离。
沙箱来自一个预热池（默认 3 个），首次使用时从池中 acquire 一个，reconciler 自动补满；
沙箱有硬性 TTL 兜底，租户闲置一段时间后回收。

通过环境变量启用/配置（见 env_example）。未启用时 terminal 工具回退到本地 subprocess，
不引入任何外部依赖，行为不变。OpenSandbox SDK 仅在启用后按需 import。
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from datetime import timedelta


def _flag(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


_ENABLED = _flag("SANDBOX_ENABLED")
_POOL_SIZE = int(os.environ.get("SANDBOX_POOL_SIZE", "3"))
_IMAGE = os.environ.get("SANDBOX_IMAGE", "python:3.12")
_DOMAIN = os.environ.get("SANDBOX_DOMAIN", "localhost:8080")
_PROTOCOL = os.environ.get("SANDBOX_PROTOCOL", "http")
_API_KEY = os.environ.get("SANDBOX_API_KEY") or None
_TTL_MIN = int(os.environ.get("SANDBOX_TIMEOUT_MINUTES", "30"))
_IDLE_MIN = int(os.environ.get("SANDBOX_IDLE_MINUTES", "15"))

_STDOUT_CAP = 8000
_STDERR_CAP = 2000


def enabled() -> bool:
    return _ENABLED


class SandboxManager:
    """维护预热池 + 每租户独占沙箱的映射，闲置回收。"""

    def __init__(self) -> None:
        self._pool = None
        self._ttl = timedelta(minutes=_TTL_MIN)
        # key -> (sandbox, last_active_monotonic)
        self._by_key: dict[str, tuple[object, float]] = {}
        self._key_locks: dict[str, asyncio.Lock] = {}
        self._lock = asyncio.Lock()
        self._reaper: asyncio.Task | None = None

    async def start(self) -> None:
        from opensandbox import InMemoryAsyncPoolStateStore, PoolCreationSpec, SandboxPoolAsync
        from opensandbox.config import ConnectionConfig

        conn = ConnectionConfig(api_key=_API_KEY, domain=_DOMAIN, protocol=_PROTOCOL)
        self._pool = SandboxPoolAsync(
            pool_name="skill-agents",
            max_idle=_POOL_SIZE,
            state_store=InMemoryAsyncPoolStateStore(),
            connection_config=conn,
            creation_spec=PoolCreationSpec(image=_IMAGE),
        )
        await self._pool.start()
        self._reaper = asyncio.create_task(self._reap_loop())

    async def shutdown(self) -> None:
        if self._reaper is not None:
            self._reaper.cancel()
        for key in list(self._by_key):
            await self.release(key)
        if self._pool is not None:
            await self._pool.shutdown()

    async def _lock_for(self, key: str) -> asyncio.Lock:
        async with self._lock:
            return self._key_locks.setdefault(key, asyncio.Lock())

    async def _get(self, key: str):
        lock = await self._lock_for(key)
        async with lock:
            entry = self._by_key.get(key)
            if entry is not None:
                sandbox, _ = entry
                # 活跃即续期，避免长会话中途被 TTL 硬杀。
                try:
                    await sandbox.renew(self._ttl)
                except Exception:
                    pass
                self._by_key[key] = (sandbox, time.monotonic())
                return sandbox
            sandbox = await self._pool.acquire(sandbox_timeout=self._ttl)
            self._by_key[key] = (sandbox, time.monotonic())
            return sandbox

    async def run(self, key: str, command: str, timeout: int = 60) -> dict:
        """在租户沙箱内执行命令，返回 {stdout, stderr, returncode}。"""
        from opensandbox.models.execd import RunCommandOpts

        sandbox = await self._get(key)
        execution = await sandbox.commands.run(
            command, opts=RunCommandOpts(timeout=timedelta(seconds=timeout))
        )
        stdout = "".join(m.text for m in execution.logs.stdout)
        stderr = "".join(m.text for m in execution.logs.stderr)
        return {
            "stdout": stdout[-_STDOUT_CAP:],
            "stderr": stderr[-_STDERR_CAP:],
            "returncode": execution.exit_code if execution.exit_code is not None else -1,
        }

    async def release(self, key: str) -> None:
        lock = await self._lock_for(key)
        async with lock:
            entry = self._by_key.pop(key, None)
        if entry is not None:
            try:
                await entry[0].kill()
            except Exception:
                pass

    async def _reap_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            cutoff = time.monotonic() - _IDLE_MIN * 60
            stale = [k for k, (_sb, ts) in list(self._by_key.items()) if ts < cutoff]
            for key in stale:
                await self.release(key)


manager = SandboxManager()


# ---------------------------------------------------------------------------
# 专用事件循环线程
# ---------------------------------------------------------------------------
# 沙箱池基于 aiohttp，其连接绑定在创建时所在的事件循环。为了让「异步的 terminal
# 工具」和「同步阻塞的 ADK code_executor.execute_code」都能安全复用同一个池，
# 池及其所有操作都跑在这个独立的后台事件循环里，两边都通过 run_coroutine_threadsafe
# 把协程投递过去：异步侧 await 结果、同步侧 .result() 阻塞等待。
_loop: asyncio.AbstractEventLoop | None = None
_thread: threading.Thread | None = None
_loop_lock = threading.Lock()


def _ensure_loop() -> asyncio.AbstractEventLoop:
    global _loop, _thread
    with _loop_lock:
        if _loop is None:
            _loop = asyncio.new_event_loop()
            _thread = threading.Thread(
                target=_loop.run_forever, name="sandbox-loop", daemon=True
            )
            _thread.start()
        return _loop


def _submit(coro):
    return asyncio.run_coroutine_threadsafe(coro, _ensure_loop())


def stop_loop() -> None:
    global _loop, _thread
    with _loop_lock:
        if _loop is not None:
            _loop.call_soon_threadsafe(_loop.stop)
            if _thread is not None:
                _thread.join(timeout=5)
            _loop.close()
            _loop = None
            _thread = None


async def start_pool() -> None:
    await asyncio.wrap_future(_submit(manager.start()))


async def stop_pool() -> None:
    try:
        await asyncio.wrap_future(_submit(manager.shutdown()))
    finally:
        stop_loop()


async def run_async(key: str, command: str, timeout: int = 60) -> dict:
    """异步侧（terminal 工具）入口：在沙箱循环上跑命令，await 结果。"""
    return await asyncio.wrap_future(_submit(manager.run(key, command, timeout)))


def run_sync(key: str, command: str, timeout: int = 60) -> dict:
    """同步侧（ADK code_executor）入口：阻塞等待沙箱循环上的命令完成。"""
    return _submit(manager.run(key, command, timeout)).result(timeout=timeout + 30)
