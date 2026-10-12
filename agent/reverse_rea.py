"""One long-lived REA process, held open so drilling into a function doesn't
re-import the binary. agent/reverse.py owns the policy (owner-only, read-only
allowlist, path safety); this file is only the transport.

The MCP stdio client builds anyio task scopes that must be entered and exited in
the SAME task, so the process is owned by a single coroutine (_serve) that opens
the transport once and then serves calls off a queue until closed. Calls are
scheduled onto that coroutine's loop from any thread; nothing else touches the
session. This mirrors agent/mcp_client._transport, which is already proven; what
is new here is only that the session stays open between calls.
"""
from __future__ import annotations

import asyncio
import threading

_loop = None
_loop_thread = None
_loop_lock = threading.Lock()


def _loop_get() -> asyncio.AbstractEventLoop:
    global _loop, _loop_thread
    with _loop_lock:
        if _loop is not None and _loop.is_running():
            return _loop
        _loop = asyncio.new_event_loop()

        def run():
            asyncio.set_event_loop(_loop)
            _loop.run_forever()

        _loop_thread = threading.Thread(target=run, daemon=True, name='ReverseREA')
        _loop_thread.start()
        return _loop


class Connection:
    """A running REA process. `call` is thread-safe and blocking; `close` ends it."""

    def __init__(self, server: dict):
        self._server = server
        self._loop = _loop_get()
        self._requests: asyncio.Queue = asyncio.Queue()
        self._ready = asyncio.run_coroutine_threadsafe(self._spawn(), self._loop)

    async def _spawn(self):
        self._started = self._loop.create_future()
        self._task = self._loop.create_task(self._serve())
        await self._started          # resolves once the session has initialized, or raises

    async def _serve(self):
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client
        env = dict(self._server.get('env') or {})
        params = StdioServerParameters(command=str(self._server['command']),
                                       args=[str(a) for a in self._server.get('args') or []],
                                       env=env or None)
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self._started.set_result(True)
                    while True:
                        tool, inputs, fut = await self._requests.get()
                        if tool is None:            # close sentinel
                            if not fut.done():
                                fut.set_result(None)
                            return
                        await self._one(session, tool, inputs, fut)
        except Exception as exc:                    # startup failed, or the process died
            if not self._started.done():
                self._started.set_exception(exc)
            self._fail_pending(exc)

    async def _one(self, session, tool, inputs, fut):
        try:
            result = await session.call_tool(tool, arguments=inputs)
            parts = []
            for c in (result.content or []):
                parts.append(c.text if hasattr(c, 'text') else str(c))
            if not fut.done():
                fut.set_result('\n'.join(parts))
        except Exception as exc:
            if not fut.done():
                fut.set_exception(exc)

    def _fail_pending(self, exc):
        while True:
            try:
                _tool, _inputs, fut = self._requests.get_nowait()
            except asyncio.QueueEmpty:
                return
            if fut is not None and not fut.done():
                fut.set_exception(exc)

    def call(self, tool: str, inputs: dict, timeout: float) -> str:
        self._ready.result(timeout=timeout)         # surface a startup failure here
        fut = asyncio.run_coroutine_threadsafe(self._enqueue(tool, inputs), self._loop)
        return fut.result(timeout=timeout)

    async def _enqueue(self, tool, inputs):
        done = self._loop.create_future()
        await self._requests.put((tool, inputs, done))
        return await done

    def close(self) -> None:
        try:
            fut = asyncio.run_coroutine_threadsafe(self._requests.put((None, None, self._loop.create_future())), self._loop)
            fut.result(timeout=10)
        except Exception:
            pass


def open_connection(server: dict, timeout: float) -> Connection:
    conn = Connection(server)
    conn._ready.result(timeout=timeout)             # wait for the session to initialize
    return conn
