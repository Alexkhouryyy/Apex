"""Authenticated, request-scoped streaming for the compact companion."""
from __future__ import annotations

import asyncio
import json
import re
import threading

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from agent import companion, conversations

router = APIRouter()
_active: dict[str, threading.Event] = {}
_threads: set[int] = set()


@router.get("/companion")
async def companion_page():
    from dashboard.server import STATIC_DIR
    return FileResponse(STATIC_DIR / "companion.html")


@router.post("/api/companion/cancel/{turn_id}")
async def cancel_turn(turn_id: str, request: Request):
    _check_origin(request)
    event = _active.get(turn_id)
    if event:
        event.set()
    return {"cancel_requested": bool(event)}


def _check_origin(request: Request):
    # Also protect tokenless localhost from cross-site simple POSTs.
    origin = request.headers.get("origin")
    if origin is not None and origin != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "Open the companion on the same origin as your Apex dashboard.")


@router.post("/api/companion/chat")
async def companion_chat(request: Request):
    _check_origin(request)
    from dashboard import server
    if not server._agent_ref:
        raise HTTPException(503, "Apex is still starting. Try again shortly.")
    # Bound the body before parsing, including clients that omit Content-Length.
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > companion.MAX_IMAGE_CHARS + 100_000:
            raise HTTPException(413, "Message or screen image is too large.")
        chunks.append(chunk)
    try:
        body = json.loads(b"".join(chunks))
        if not isinstance(body, dict):
            raise ValueError("Expected a message object.")
        message = body.get("message")
        mode = body.get("mode", "discuss")
        turn_id = body.get("turn_id")
        if not isinstance(message, str) or not 1 <= len(message.strip()) <= 20_000:
            raise ValueError("Enter a message of 1–20,000 characters.")
        if not isinstance(turn_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{16,80}", turn_id):
            raise ValueError("Invalid turn identifier.")
        companion.prompt(mode, bool(body.get("screen_image")))
        companion.validate_screen_image(body.get("screen_image"))
        thread_id = body.get("thread_id")
        if thread_id is not None:
            if type(thread_id) is not int or thread_id < 1:
                raise ValueError("Invalid conversation identifier.")
            if not conversations.exists(thread_id):
                raise ValueError("Conversation no longer exists. Start a new conversation.")
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc

    if turn_id in _active or thread_id in _threads:
        raise HTTPException(409, "This conversation is already working. Stop it or wait for the reply.")
    if len(_active) >= 8:
        raise HTTPException(429, "Apex is busy. Try again shortly.")
    if thread_id is None:
        thread_id = conversations.create("Screen companion")
    cancel = threading.Event()
    _active[turn_id] = cancel
    _threads.add(thread_id)
    agent = server._agent_ref
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue()

    def emit(event):
        if not cancel.is_set():
            loop.call_soon_threadsafe(queue.put_nowait, event)

    class Streamer:
        def start(self): pass
        def finish(self): pass
        def feed(self, text): emit({"type": "token", "text": text})
        def tool(self, event): emit({"type": "tool", **event})

    def run():
        try:
            channel_id = f"companion:{thread_id}"
            memory, lock = agent._get_channel(channel_id)
            with lock:
                if not memory.messages:
                    # Restore text history after a server restart. Screen frames
                    # are deliberately not written to the conversation table.
                    for item in conversations.messages(thread_id, limit=30, newest=True):
                        if item["role"] == "user":
                            memory.add_user(item["text"])
                        else:
                            memory.add_assistant([{"type": "text", "text": item["text"]}])
            conversations.add_message(thread_id, "user", message.strip())
            response = agent.run(
                message.strip(), include_screenshot=False, streamer=Streamer(),
                channel_id=channel_id, cancel_event=cancel,
                companion_mode=mode, screen_image=body.get("screen_image"),
            )
            if cancel.is_set():
                response = (response or "") + "\n[Interrupted; any completed actions remain in effect.]"
            conversations.add_message(thread_id, "agent", response)
            # Completion must reach the client even after an explicit stop.
            loop.call_soon_threadsafe(queue.put_nowait, {
                "type": "done", "text": response, "interrupted": cancel.is_set(),
            })
        except Exception as exc:
            loop.call_soon_threadsafe(queue.put_nowait, {"type": "error", "text": str(exc)})
        finally:
            # Hold the busy guard until the worker exits, even after disconnect.
            loop.call_soon_threadsafe(_active.pop, turn_id, None)
            loop.call_soon_threadsafe(_threads.discard, thread_id)

    async def events():
        worker = None
        try:
            yield json.dumps({"type": "start", "thread_id": thread_id}) + "\n"
            worker = loop.run_in_executor(None, run)
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield '{"type":"heartbeat"}\n'
                    continue
                yield json.dumps(event) + "\n"
                if event["type"] in {"done", "error"}:
                    break
        finally:
            cancel.set()
            if worker is None:
                _active.pop(turn_id, None)
                _threads.discard(thread_id)

    return StreamingResponse(events(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
