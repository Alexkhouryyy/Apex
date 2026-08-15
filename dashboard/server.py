"""FastAPI dashboard for the voice AI agent.

Read/write endpoints over the existing modules (longterm, scheduler,
knowledge, orchestrator, self_mod, goals). WebSocket pushes live activity
events from the awareness log.

Runs in a background thread on DASHBOARD_PORT (default 7860).
"""
import asyncio
import hmac
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

import config
from agent import longterm, knowledge, self_mod, goals, orchestrator
from agent import scheduler as sched
from agent import briefing as briefing_mod
from agent import entities as ent_mod
from agent import reflection as refl_mod
from agent import telemetry as tel_mod
from agent import feedback as fb_mod
from agent import outcomes as outcomes_mod
from agent import rollback as rollback_mod
from agent import budget as budget_mod
from tools import phone as phone_mod
from tools import telegram as telegram_mod
from tools import discord as discord_mod
from tools import slack as slack_mod
from tools import whatsapp as whatsapp_mod
from tools import signal as signal_mod
from tools import iot as iot_mod
from agent import iot as iot_state

STATIC_DIR = Path(__file__).parent / "static"

# Will be set when the dashboard is started — gives us a handle to the live agent state
_agent_ref = None
_awareness_log = None


def set_agent(agent, awareness_log=None) -> None:
    global _agent_ref, _awareness_log
    _agent_ref = agent
    _awareness_log = awareness_log


# === WebSocket connection manager ===
class WSManager:
    def __init__(self):
        self.active: list[WebSocket] = []
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    def broadcast_threadsafe(self, payload: dict) -> None:
        """Called from any thread — schedules a broadcast on the dashboard's loop."""
        if not self.loop or not self.active:
            return
        try:
            asyncio.run_coroutine_threadsafe(self._broadcast(payload), self.loop)
        except Exception:
            pass

    async def _broadcast(self, payload: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


ws_manager = WSManager()

# Live-refresh open Documents tabs on any write (editor save OR agent document_write).
try:
    from agent import documents as _documents_mod
    _documents_mod.set_broadcaster(ws_manager.broadcast_threadsafe)
except Exception:
    pass

_chat_lock: Optional[asyncio.Lock] = None


class ChatStreamer:
    """Minimal streamer that forwards token deltas to dashboard WebSocket clients."""
    def __init__(self, chat_id: str):
        self.chat_id = chat_id

    def feed(self, text: str):
        ws_manager.broadcast_threadsafe({
            "type": "chat_token",
            "delta": text,
            "chat_id": self.chat_id,
        })

    def start(self): pass
    def finish(self): pass


# === FastAPI app ===
app = FastAPI(title="Voice Agent Dashboard")

# Allow the browser extension (chrome-extension:// / moz-extension://) to call the
# API cross-origin. Auth is bearer-token (not cookies), so credentials stay off.
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(chrome-extension|moz-extension)://.*$",
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=False,
)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


_WEBHOOK_PATHS = frozenset({
    "/telegram/webhook", "/discord/interactions",
    "/twilio/sms", "/twilio/voice", "/twilio/whatsapp",
    "/slack/events", "/signal/webhook", "/iot/webhook",
})


# Brute-force throttle: too many bad tokens from one IP → cool that IP off.
from dashboard.ratelimit import AuthThrottle

_throttle = AuthThrottle(window=60.0, max_fails=10)


@app.middleware("http")
async def _auth(request: Request, call_next):
    # CORS preflight carries no Authorization header — let it through so the
    # CORSMiddleware can answer it.
    if request.method == "OPTIONS":
        return await call_next(request)
    token = config.DASHBOARD_TOKEN
    if not token:
        return await call_next(request)
    path = request.url.path
    # Allow the SPA shell + static assets so the login overlay can load.
    # PWA entry points (manifest + service worker) must also load pre-auth so the
    # app can install and the SW can control the origin before a token is entered.
    if (path == "/" or path.startswith("/static/") or path == "/health"
            or path == "/sw.js" or path == "/manifest.webmanifest"):
        return await call_next(request)
    # Inbound webhooks carry per-service auth; don't block them here.
    if path in _WEBHOOK_PATHS:
        return await call_next(request)
    ip = request.client.host if request.client else "?"
    if _throttle.is_locked(ip):
        return Response("Too many attempts. Try again later.", status_code=429)
    auth = request.headers.get("Authorization", "")
    provided = auth[7:] if auth.startswith("Bearer ") else ""
    if provided:
        # The shared DASHBOARD_TOKEN is the master credential; per-device tokens
        # are revocable peers. Mark which one authenticated so management endpoints
        # can require master.
        if hmac.compare_digest(provided, token):
            request.state.is_master = True
            return await call_next(request)
        try:
            from agent import access_tokens
            if access_tokens.verify(provided):
                request.state.is_master = False
                return await call_next(request)
        except Exception:
            pass
    _throttle.record_failure(ip)
    return Response("Unauthorized", status_code=401)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return HTMLResponse("<h1>Dashboard static files missing</h1>")


# --- PWA entry points (served from root scope, not /static/) ---
@app.get("/sw.js")
async def service_worker():
    # The service worker must be served from the origin root so its scope can
    # control the whole app (a /static/ SW could only control /static/).
    sw = STATIC_DIR / "sw.js"
    if sw.exists():
        return FileResponse(str(sw), media_type="application/javascript",
                            headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})
    return Response("// not found", status_code=404, media_type="application/javascript")


@app.get("/manifest.webmanifest")
async def web_manifest():
    mf = STATIC_DIR / "manifest.webmanifest"
    if mf.exists():
        return FileResponse(str(mf), media_type="application/manifest+json")
    return JSONResponse({"error": "manifest missing"}, status_code=404)


# --- Status ---
@app.get("/api/status")
def status():
    try:
        from tools import sandbox
        exec_backend = sandbox.active_backend_name()
    except Exception:
        exec_backend = "local"
    return {
        "model": _agent_ref._model if _agent_ref else config.AGENT_MODEL,
        "proactive_enabled": config.PROACTIVE_ENABLED,
        "awareness_enabled": config.AWARENESS_ENABLED,
        "tools_count": len(_agent_ref._all_tools()) if _agent_ref else 0,
        "exec_backend": exec_backend,
        "sandboxed": exec_backend in ("docker", "refusing"),
        "uptime_s": int(time.time() - _START_TIME),
    }


# --- Models ---
@app.get("/api/models")
def list_models():
    from agent.provider import KNOWN_MODELS, provider_for
    have_key = {
        "anthropic": bool(config.ANTHROPIC_API_KEY),
        "openai": bool(config.OPENAI_API_KEY),
        "gemini": bool(config.GEMINI_API_KEY),
        "ollama": bool(config.OLLAMA_BASE_URL),
    }
    models = [
        {"model": m, "provider": provider_for(m), "available": have_key.get(provider_for(m), False)}
        for m in sorted(KNOWN_MODELS)
    ]
    return {
        "current": _agent_ref._model if _agent_ref else config.AGENT_MODEL,
        "models": models,
    }


@app.post("/api/model")
async def set_model_endpoint(request: Request):
    if not _agent_ref:
        return JSONResponse({"error": "agent not ready"}, status_code=503)
    body = await request.json()
    model = (body.get("model") or "").strip()
    if not model:
        return JSONResponse({"error": "no model given"}, status_code=400)
    message = _agent_ref.set_model(model)
    ok = message.startswith("Switched")
    return JSONResponse(
        {"ok": ok, "message": message, "model": _agent_ref._model},
        status_code=200 if ok else 400,
    )


# --- Memories ---
@app.get("/api/memories")
def list_memories(q: str = "", kind: str = "", limit: int = 50):
    return longterm.recall(query=q, kind=kind, limit=limit, semantic=bool(q))


@app.post("/api/memories")
def add_memory(payload: dict):
    return {"result": longterm.remember(
        payload["content"],
        kind=payload.get("kind", "fact"),
        importance=payload.get("importance", 5),
        tags=payload.get("tags", ""),
    )}


@app.delete("/api/memories/{memory_id}")
def delete_memory(memory_id: int):
    return {"result": longterm.forget(memory_id)}


# --- Scheduler ---
@app.get("/api/tasks")
def list_tasks():
    return sched.list_tasks()


@app.post("/api/tasks")
def add_task(payload: dict):
    return {"result": sched.schedule(
        description=payload["description"],
        trigger_type=payload["trigger_type"],
        trigger_params=payload["trigger_params"],
    )}


@app.delete("/api/tasks/{task_id}")
def cancel_task(task_id: str):
    return {"result": sched.cancel(task_id)}


# --- Knowledge ---
@app.get("/api/knowledge/stats")
def kb_stats():
    return knowledge.stats()


@app.post("/api/knowledge/reindex")
def kb_reindex(payload: dict):
    return {"result": knowledge.reindex(payload["paths"], force=payload.get("force", False))}


@app.get("/api/knowledge/search")
def kb_search(q: str, k: int = 6):
    return knowledge.search(q, top_k=k)


# --- Goals ---
@app.get("/api/goals")
def list_goals_endpoint(active_only: bool = True):
    return goals.list_goals(active_only=active_only)


@app.post("/api/goals")
def add_goal(payload: dict):
    return {"result": goals.set_goal(
        title=payload["title"],
        description=payload.get("description", ""),
        horizon=payload.get("horizon", "week"),
        deadline_iso=payload.get("deadline_iso"),
    )}


@app.patch("/api/goals/{goal_id}")
def update_goal_endpoint(goal_id: int, payload: dict):
    return {"result": goals.update_goal(
        goal_id,
        status=payload.get("status"),
        progress_note=payload.get("progress_note"),
        score=payload.get("score"),
        # Without this an llm-kind completion contract sees "(none gathered)" and
        # correctly refuses, making contracted goals unclosable from the UI.
        evidence=payload.get("evidence", ""),
    )}


@app.get("/api/goals/{goal_id}/verification")
def goal_verification(goal_id: int):
    """A goal's completion contracts and its evidence ledger.

    Surfaces verification.history(), which was written on every check and never
    read back — so a refused close gave no way to see WHY. A gate whose refusals
    are invisible is worse than no gate.
    """
    try:
        from agent import verification
        return {
            "contracts": verification.list_contracts(goal_id),
            "history": verification.history(goal_id),
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# --- Sub-agents ---
@app.get("/api/subagents")
def list_subagents():
    out = {}
    for sid, info in orchestrator.list_all().items():
        out[sid] = {
            "role": info.get("role"),
            "task": (info.get("task") or "")[:140],
            "status": info.get("status"),
            "started": info.get("started"),
            "ended": info.get("ended"),
            "result_preview": (info.get("result") or "")[:200] if info.get("result") else None,
            "error": info.get("error"),
        }
    return out


# --- Self-mod ---
@app.get("/api/selfmod")
def get_selfmod():
    return self_mod.show()


@app.post("/api/selfmod/prompt")
def update_prompt(payload: dict):
    return {"result": self_mod.update_system_prompt(payload["addition"], replace=payload.get("replace", False))}


@app.post("/api/selfmod/revert")
def revert_selfmod(payload: dict = None):
    return {"result": self_mod.revert(restore_backup=(payload or {}).get("restore_backup", False))}


# --- Awareness events ---
@app.get("/api/events")
def recent_events(seconds: float = 300.0):
    if _awareness_log is None:
        return []
    return _awareness_log.recent(since_seconds=seconds)


# --- Tier-4: Reflections ---
@app.get("/api/reflections")
def list_reflections_endpoint(status: str = "pending", limit: int = 100):
    return refl_mod.list_reflections(status=status, limit=limit)


@app.post("/api/reflections/{reflection_id}/apply")
def apply_reflection_endpoint(reflection_id: int, payload: dict = None):
    accept = (payload or {}).get("accept", True)
    return {"result": refl_mod.apply_reflection(reflection_id, accept=accept)}


@app.post("/api/reflections/run")
def run_reflection_endpoint(payload: dict = None):
    import anthropic
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    hours = (payload or {}).get("hours", 24)
    return refl_mod.consolidate(client, hours=int(hours))


# --- Tier-4: Knowledge Graph ---
@app.get("/api/entities")
def list_entities(kind: str = "", limit: int = 100):
    if kind:
        return ent_mod.query_by_kind(kind, limit=limit)
    return ent_mod.subgraph(limit_nodes=limit)


@app.get("/api/entities/query")
def query_entity_endpoint(name: str, hops: int = 1):
    return ent_mod.query_entity(name, hops=hops)


@app.post("/api/entities")
def upsert_entity_endpoint(payload: dict):
    return ent_mod.upsert_entity(
        payload["name"],
        kind=payload.get("kind", "concept"),
        properties=payload.get("properties") or {},
        importance=int(payload.get("importance", 5)),
    )


@app.post("/api/entities/relate")
def relate_endpoint(payload: dict):
    return ent_mod.relate(
        payload["from_name"], payload["to_name"], payload["kind"],
        from_kind=payload.get("from_kind", "concept"),
        to_kind=payload.get("to_kind", "concept"),
        properties=payload.get("properties") or {},
        confidence=float(payload.get("confidence", 1.0)),
    )


@app.delete("/api/entities/{entity_id}")
def delete_entity_endpoint(entity_id: int):
    return {"result": ent_mod.delete_entity(entity_id)}


# --- Tier-4: Telemetry & Replay ---
@app.get("/api/telemetry")
def telemetry_summary(days: int = 7):
    return tel_mod.summary(days=days)


@app.get("/api/telemetry/sessions")
def telemetry_sessions(limit: int = 30):
    return tel_mod.list_recent_sessions(limit=limit)


@app.get("/api/budget")
def budget_get():
    cfg = budget_mod.get_config()
    return {
        **cfg,
        "today_spend":   budget_mod.today_spend(),
        "session_spend": budget_mod.session_spend(),
    }


@app.post("/api/budget")
async def budget_post(request: Request):
    body = await request.json()
    allowed = {"daily_usd", "session_usd", "enabled"}
    budget_mod.set_config({k: v for k, v in body.items() if k in allowed})
    return {"ok": True}


@app.get("/api/replay/{session_id}")
def replay_session_endpoint(session_id: int):
    return tel_mod.replay_session(session_id)


@app.get("/api/turns/search")
def search_turns_endpoint(q: str, limit: int = 20, session_id: int = None):
    return longterm.search_turns(q, limit=limit, session_id=session_id)


# --- Phase 7: User feedback (👍/👎) on completed turns ---
@app.post("/api/feedback")
async def feedback_endpoint(request: Request):
    body = await request.json()
    try:
        rating = int(body.get("rating"))
        session_id = int(body.get("session_id"))
        turn_index = int(body.get("turn_index"))
    except (TypeError, ValueError):
        return JSONResponse(
            {"error": "rating, session_id, turn_index are required ints"},
            status_code=400,
        )
    try:
        row = fb_mod.record(
            rating,
            session_id=session_id,
            turn_index=turn_index,
            comment=(body.get("comment") or "").strip(),
            source=(body.get("source") or "dashboard"),
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"ok": True, "feedback": row}


@app.get("/api/feedback/recent")
def feedback_recent(limit: int = 50, days: int = 30):
    return fb_mod.recent(limit=limit, days=days)


@app.get("/api/feedback/summary")
def feedback_summary_endpoint(days: int = 7):
    return fb_mod.summary(days=days)


@app.get("/api/feedback/turn")
def feedback_for_turn(session_id: int, turn_index: int):
    row = fb_mod.for_turn(session_id, turn_index)
    return row or {}


# --- Phase 7: Outcome tracking ---
@app.get("/api/outcomes/overall")
def outcomes_overall(days: int = 7):
    return outcomes_mod.overall(days=days)


@app.get("/api/outcomes/skills")
def outcomes_skills(days: int = 7, name: str = ""):
    return outcomes_mod.skill_outcomes(name=name or None, days=days)


@app.get("/api/outcomes/reflections")
def outcomes_reflections(days: int = 30, window_hours: int = 168):
    return outcomes_mod.reflection_outcomes(days=days, window_hours=window_hours)


@app.get("/api/outcomes/rewrites")
def list_rewrites_endpoint(days: int = 30):
    return rollback_mod.list_rewrites(days=days)


@app.post("/api/outcomes/check-rollback")
async def check_rollback_endpoint(request: Request):
    body = {}
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            body = await request.json()
        except Exception:
            pass
    dry_run = (body or {}).get("dry_run", False)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: rollback_mod.check_rewrites(dry_run=dry_run))
    ws_manager.broadcast_threadsafe({"type": "rollback_done", **result})
    return result


# --- Tier-4: Phone (Twilio webhooks + status) ---
@app.get("/api/phone/status")
def phone_status():
    return {
        "configured": bool(getattr(config, "TWILIO_SID", "")) and bool(getattr(config, "TWILIO_AUTH_TOKEN", "")),
        "from_number": getattr(config, "TWILIO_FROM_NUMBER", ""),
        "allowed_numbers": getattr(config, "PHONE_ALLOWED_NUMBERS", []),
    }


@app.post("/api/phone/sms")
def phone_sms_endpoint(payload: dict):
    return {"result": phone_mod.sms_send(payload["to"], payload["body"])}


@app.post("/twilio/sms")
async def twilio_inbound_sms(request: Request):
    form = await request.form()
    from_number = form.get("From", "")
    body = form.get("Body", "")
    twiml = phone_mod.dispatch_inbound_sms(from_number, body)
    return Response(content=twiml, media_type="application/xml")


@app.post("/twilio/voice")
async def twilio_inbound_voice(request: Request):
    form = await request.form()
    from_number = form.get("From", "")
    speech_result = form.get("SpeechResult", "")
    twiml = phone_mod.dispatch_inbound_voice(from_number, speech_result or None)
    return Response(content=twiml, media_type="application/xml")


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    update = await request.json()
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, lambda: telegram_mod.dispatch_inbound(update))
    return {"ok": True}


@app.get("/api/telegram/status")
def telegram_status():
    return {
        "configured": telegram_mod.is_configured(),
        "allowed_chat_ids": getattr(config, "TELEGRAM_ALLOWED_CHAT_IDS", []),
    }


@app.post("/discord/interactions")
async def discord_interactions(request: Request):
    body = await request.body()
    sig = request.headers.get("X-Signature-Ed25519", "")
    ts = request.headers.get("X-Signature-Timestamp", "")
    if not discord_mod.verify_signature(sig, ts, body):
        return Response(content="invalid request signature", status_code=401)
    interaction = json.loads(body)
    return JSONResponse(discord_mod.dispatch_interaction(interaction))


@app.get("/api/discord/status")
def discord_status():
    return {
        "configured": discord_mod.is_configured(),
        "allowed_user_ids": getattr(config, "DISCORD_ALLOWED_USER_IDS", []),
    }


_slack_seen_events: "collections.OrderedDict[str, bool]" = __import__("collections").OrderedDict()


def _slack_dedup(event_id: str) -> bool:
    """True if this Slack event_id was already handled (Slack retries on slow ack)."""
    if not event_id:
        return False
    if event_id in _slack_seen_events:
        return True
    _slack_seen_events[event_id] = True
    while len(_slack_seen_events) > 512:
        _slack_seen_events.popitem(last=False)
    return False


@app.post("/slack/events")
async def slack_events(request: Request):
    body = await request.body()
    sig = request.headers.get("X-Slack-Signature", "")
    ts = request.headers.get("X-Slack-Request-Timestamp", "")
    if not slack_mod.verify_signature(sig, ts, body):
        return Response(content="invalid signature", status_code=401)
    payload = json.loads(body)
    # URL verification handshake — answer inline, never run the agent.
    if payload.get("type") == "url_verification":
        return JSONResponse({"challenge": payload.get("challenge", "")})
    # Dedup Slack retries (they resend if we don't ack within ~3s).
    if _slack_dedup(payload.get("event_id", "")):
        return {"ok": True}
    # Run the agent EXACTLY ONCE, off the event loop, and ack immediately.
    # (Previously this ran dispatch_event synchronously — blocking the loop — and
    # then AGAIN in the executor, causing duplicate replies + double model charges.)
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, lambda: slack_mod.dispatch_event(payload))
    return {"ok": True}


@app.get("/api/slack/status")
def slack_status():
    return {
        "configured": slack_mod.is_configured(),
        "allowed_channel_ids": getattr(config, "SLACK_ALLOWED_CHANNEL_IDS", []),
    }


@app.post("/twilio/whatsapp")
async def twilio_whatsapp(request: Request):
    form = await request.form()
    twiml = whatsapp_mod.dispatch_inbound(dict(form))
    return Response(content=twiml, media_type="application/xml")


@app.get("/api/whatsapp/status")
def whatsapp_status():
    return {
        "configured": whatsapp_mod.is_configured(),
        "from_number": getattr(config, "WHATSAPP_FROM_NUMBER", ""),
        "allowed_numbers": getattr(config, "WHATSAPP_ALLOWED_NUMBERS", []),
    }


@app.post("/signal/webhook")
async def signal_webhook(request: Request):
    payload = await request.json()
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, lambda: signal_mod.dispatch_inbound(payload))
    return {"ok": True}


@app.get("/api/signal/status")
def signal_status():
    return {
        "configured": signal_mod.is_configured(),
        "phone_number": getattr(config, "SIGNAL_PHONE_NUMBER", ""),
        "allowed_numbers": getattr(config, "SIGNAL_ALLOWED_NUMBERS", []),
    }


# --- IoT ---
@app.post("/iot/webhook")
async def iot_webhook(request: Request):
    body = await request.body()
    sig = request.headers.get("X-Apex-Signature", "")
    if not iot_state.verify_signature(sig or None, body):
        return JSONResponse({"error": "invalid signature"}, status_code=403)
    if not iot_state.is_enabled():
        return JSONResponse({"error": "IoT is disabled"}, status_code=503)
    try:
        payload = json.loads(body)
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, lambda: iot_mod.dispatch_inbound(payload))
    return {"ok": True}


@app.get("/api/iot/status")
def iot_status():
    return {
        "env_enabled": config.IOT_ENABLED,
        "runtime_enabled": iot_state.is_enabled(),
        "ha_url": config.IOT_HA_URL or "",
        "ha_configured": bool(config.IOT_HA_URL and config.IOT_HA_TOKEN),
        "awareness_entities": config.IOT_AWARENESS_ENTITIES,
        "trigger_entities": config.IOT_TRIGGER_ALLOWED_ENTITIES,
        "webhook_secret_set": bool(config.IOT_WEBHOOK_SECRET),
    }


@app.post("/api/iot/toggle")
async def iot_toggle(request: Request):
    body = await request.json()
    value = bool(body.get("enabled", not iot_state.is_enabled()))
    iot_state.set_enabled(value, source="dashboard")
    return {"ok": True, "enabled": value}


# --- Camera / Vision ---
@app.get("/api/camera/status")
def camera_status():
    from tools import camera as _cam
    try:
        import cv2  # noqa: F401
        cv2_available = True
    except ImportError:
        cv2_available = False
    return {
        "enabled": _cam.is_enabled(),
        "device_index": getattr(config, "CAMERA_DEVICE_INDEX", 0),
        "cv2_available": cv2_available,
    }


@app.post("/api/camera/toggle")
async def camera_toggle(request: Request):
    body = await request.json()
    enabled = bool(body.get("enabled"))
    config.CAMERA_ENABLED = enabled
    return {"ok": True, "enabled": enabled}


@app.get("/api/camera/frame")
def camera_frame():
    from tools import camera as _cam
    try:
        b64, (w, h) = _cam.capture()
        return {"ok": True, "image": b64, "width": w, "height": h}
    except RuntimeError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=200)


# --- Guardian Angel ---
_guardian_ref = None


def set_guardian(guardian) -> None:
    global _guardian_ref
    _guardian_ref = guardian


@app.get("/api/guardian")
def guardian_get():
    enabled = getattr(config, "GUARDIAN_ANGEL_ENABLED", True)
    log = _guardian_ref.recent_log(10) if _guardian_ref else []
    return {"enabled": enabled, "log": log}


@app.post("/api/guardian/toggle")
async def guardian_toggle(request: Request):
    body = await request.json()
    value = bool(body.get("enabled", True))
    config.GUARDIAN_ANGEL_ENABLED = value
    if _guardian_ref:
        _guardian_ref.set_enabled(value)
    return {"ok": True, "enabled": value}


# --- Time Capsule ---
_timecapsule_ref = None


def set_timecapsule(timecapsule) -> None:
    global _timecapsule_ref
    _timecapsule_ref = timecapsule


@app.get("/api/timecapsule")
def timecapsule_get():
    enabled = getattr(config, "TIME_CAPSULE_ENABLED", True)
    log = _timecapsule_ref.recent_capsules(10) if _timecapsule_ref else []
    return {"enabled": enabled, "log": log}


@app.post("/api/timecapsule/toggle")
async def timecapsule_toggle(request: Request):
    body = await request.json()
    value = bool(body.get("enabled", True))
    config.TIME_CAPSULE_ENABLED = value
    if _timecapsule_ref:
        _timecapsule_ref.set_enabled(value)
    return {"ok": True, "enabled": value}


# --- Web Push (cross-device proactive notifications) ---
from agent import notify as notify_mod


@app.get("/api/push/vapid")
def push_vapid():
    """Public VAPID key the browser needs to subscribe (safe to expose)."""
    return {"publicKey": getattr(config, "VAPID_PUBLIC_KEY", ""),
            "enabled": bool(getattr(config, "VAPID_PRIVATE_KEY", ""))}


@app.post("/api/push/subscribe")
async def push_subscribe(request: Request):
    body = await request.json()
    sub = body.get("subscription") or body
    label = body.get("device_label", "")
    device_id = body.get("device_id", "")
    try:
        sub_id = notify_mod.add_subscription(sub, device_label=label, device_id=device_id)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"ok": True, "id": sub_id}


@app.post("/api/push/unsubscribe")
async def push_unsubscribe(request: Request):
    body = await request.json()
    endpoint = (body.get("endpoint") or "").strip()
    if endpoint:
        notify_mod.remove_subscription(endpoint)
    return {"ok": True}


@app.post("/api/push/test")
async def push_test():
    """Send a test notification through the hub to every device."""
    notify_mod.notify("Apex", "Notifications are working. You'll hear from me here.",
                      kind="info", url="/", dedup_key=None)
    return {"ok": True, "subscriptions": len(notify_mod.list_subscriptions())}


# --- Devices + pairing (cross-device presence) ---
from agent import devices as devices_mod


@app.get("/api/devices")
def devices_list():
    return {"devices": devices_mod.list_devices(), "active": devices_mod.active_device_id()}


@app.delete("/api/devices/{device_id}")
def devices_forget(device_id: str):
    devices_mod.forget(device_id)
    return {"ok": True}


def _pair_url(request: Request) -> str:
    """Build the URL a phone should open to pair: <base>/#token=<device_token>.

    Mints a fresh REVOCABLE device token — never embeds the master DASHBOARD_TOKEN,
    so a paired phone can be revoked individually and a leaked QR/URL never exposes
    the master secret. Callers must already be master (enforced at the endpoints).
    """
    base = (getattr(config, "PUBLIC_BASE_URL", "") or "").rstrip("/")
    if not base:
        base = str(request.base_url).rstrip("/")
    if not config.DASHBOARD_TOKEN:
        return f"{base}/?source=pair"  # auth disabled; nothing to embed
    from agent import access_tokens
    token = access_tokens.issue(label="paired device")
    return f"{base}/?source=pair#token={token}"


@app.get("/api/pair/info")
def pair_info(request: Request):
    if config.DASHBOARD_TOKEN and not _require_master(request):
        return JSONResponse({"error": "Only the master token can create a pairing link."}, status_code=403)
    return {"url": _pair_url(request),
            "base": (getattr(config, "PUBLIC_BASE_URL", "") or str(request.base_url).rstrip("/"))}


# --- Per-device access tokens (revocable; managed only by the master token) ---
def _require_master(request: Request) -> bool:
    """Device-scoped tokens may use the app but not mint/revoke other tokens."""
    return getattr(request.state, "is_master", False)


@app.get("/api/auth/tokens")
def auth_tokens_list(request: Request):
    if not _require_master(request):
        return JSONResponse({"error": "Only the master token can manage device tokens."}, status_code=403)
    from agent import access_tokens
    return {"tokens": access_tokens.list_tokens()}


@app.post("/api/auth/tokens")
async def auth_tokens_issue(request: Request):
    if not _require_master(request):
        return JSONResponse({"error": "Only the master token can mint device tokens."}, status_code=403)
    body = await request.json()
    label = (body.get("label") or "").strip()
    from agent import access_tokens
    token = access_tokens.issue(label=label)
    # Build a pairing URL that carries the NEW device token (not the master secret).
    base = (getattr(config, "PUBLIC_BASE_URL", "") or str(request.base_url).rstrip("/")).rstrip("/")
    pair_url = f"{base}/?source=pair#token={token}"
    # Returned ONCE — the raw token is not recoverable afterwards.
    return {"token": token, "pair_url": pair_url, "label": label}


@app.post("/api/auth/tokens/{token_id}/revoke")
def auth_tokens_revoke(token_id: int, request: Request):
    if not _require_master(request):
        return JSONResponse({"error": "Only the master token can revoke device tokens."}, status_code=403)
    from agent import access_tokens
    ok = access_tokens.revoke(token_id)
    return {"ok": ok}


@app.get("/api/pending-actions")
def pending_actions_list():
    """List cortex actions awaiting user approval."""
    try:
        from agent import cortex as _cortex
        return {"actions": _cortex.list_pending("pending")}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/pending-actions/{action_id}/approve")
def pending_actions_approve(action_id: int):
    try:
        from agent import cortex as _cortex
        result = _cortex.approve_action(action_id)
        return {"ok": True, "result": result}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/pending-actions/{action_id}/reject")
def pending_actions_reject(action_id: int):
    try:
        from agent import cortex as _cortex
        result = _cortex.reject_action(action_id)
        return {"ok": True, "result": result}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/forged-tools")
def forged_tools_list():
    """List tools that the skill forge has written."""
    try:
        from agent import skill_forge as _forge
        return {"tools": _forge.list_forged()}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/email/inbox")
def email_inbox_endpoint(limit: int = 20, unread_only: bool = False):
    """Recent inbox messages. Returns {configured, messages}."""
    try:
        from tools import email_box
        if not email_box.is_configured():
            return {"configured": False, "messages": []}
        return {"configured": True, "messages": email_box.fetch_inbox(limit=limit, unread_only=unread_only)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/email/message/{uid}")
def email_message_endpoint(uid: str):
    try:
        from tools import email_box
        return email_box.read_message(uid)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/email/triage")
async def email_triage_endpoint(request: Request):
    """Run the email_triage skill and return its text report."""
    try:
        body = {}
        try:
            body = await request.json()
        except Exception:
            pass
        from agent import skills as _skills
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: _skills.run_skill("email_triage", {
                "limit": int(body.get("limit", 12)),
                "unread_only": bool(body.get("unread_only", True)),
            }),
        )
        return {"report": result}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/calendar/events")
async def calendar_events_endpoint(days_ahead: int = 7):
    """Upcoming CalDAV events. Returns {configured, events}."""
    try:
        from tools import calendar_box
        if not calendar_box.is_configured():
            return {"configured": False, "events": []}
        loop = asyncio.get_event_loop()
        events = await loop.run_in_executor(
            None, lambda: calendar_box.upcoming_events(days_ahead=days_ahead)
        )
        return {"configured": True, "events": events}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/evolution")
def evolution_ledger(days: int = 30):
    """Unified self-improvement ledger: every skill Apex created, refined, or rolled back.

    Aggregates three streams into one reverse-chronological timeline so the user can
    watch Apex compound over time:
      - forged/created skills  (skill_forge.forged_tools)
      - refinements + rollbacks (skill_rewrites via rollback.list_rewrites)
      - currently-failing skills queued for the next nightly refine pass
    """
    try:
        from agent import skills as _skills
        from agent import skill_forge as _forge

        events: list[dict] = []

        # Rewrites: each is a 'refined' event; rolled_back ones also emit a 'rolled_back' event.
        rewrites = rollback_mod.list_rewrites(days=days)
        for r in rewrites:
            events.append({
                "ts": r.get("ts"),
                "kind": "refined",
                "name": r.get("name"),
                "trigger": r.get("trigger"),
                "delta": r.get("delta"),
                "status": r.get("status"),
                "detail": (
                    f"approval {_pct(r.get('pre_approval_rate'))} → {_pct(r.get('post_approval_rate'))}"
                    if r.get("post_approval_rate") is not None else
                    f"rewritten ({r.get('trigger') or 'manual'})"
                ),
            })
            if r.get("status") == "rolled_back":
                events.append({
                    "ts": r.get("rollback_ts") or r.get("ts"),
                    "kind": "rolled_back",
                    "name": r.get("name"),
                    "trigger": r.get("trigger"),
                    "delta": r.get("delta"),
                    "status": "rolled_back",
                    "detail": r.get("rollback_reason") or "reverted — rewrite hurt approval rate",
                })

        # Forged/created skills.
        cutoff = time.time() - days * 86400
        for t in _forge.list_forged():
            created = t.get("created_at") or 0
            if created < cutoff:
                continue
            events.append({
                "ts": created,
                "kind": "created",
                "name": t.get("name"),
                "trigger": "forge",
                "status": t.get("status"),
                "needs_network": t.get("needs_network"),
                "detail": t.get("description") or "new skill forged",
            })

        events.sort(key=lambda e: e.get("ts") or 0, reverse=True)

        # Failing skills queued for the next nightly refine pass.
        failing = _skills.failure_stats(hours=days * 24, min_failures=3)

        installed = _skills.list_skills()
        refined_n = sum(1 for e in events if e["kind"] == "refined")
        created_n = sum(1 for e in events if e["kind"] == "created")
        rolled_n = sum(1 for e in events if e["kind"] == "rolled_back")

        return {
            "summary": {
                "installed": len(installed),
                "created": created_n,
                "refined": refined_n,
                "rolled_back": rolled_n,
                "failing_now": len(failing),
                "window_days": days,
            },
            "events": events[:200],
            "failing": failing,
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


def _pct(rate) -> str:
    try:
        return f"{rate * 100:.0f}%" if rate is not None else "n/a"
    except Exception:
        return "n/a"


@app.post("/api/forged-tools/{tool_id}/approve")
def forged_tools_approve(tool_id: int):
    try:
        from agent import skill_forge as _forge
        result = _forge.approve_forged(tool_id)
        return {"ok": True, "result": result}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/forged-tools/{tool_id}/reject")
def forged_tools_reject(tool_id: int):
    try:
        from agent import skill_forge as _forge
        result = _forge.reject_forged(tool_id)
        return {"ok": True, "result": result}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/staged-writes")
def staged_writes_list():
    """List memory/note/skill writes awaiting user approval."""
    try:
        from agent import approvals as _appr
        return {"writes": _appr.list_pending("pending")}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/staged-writes/{write_id}/approve")
def staged_writes_approve(write_id: str):
    try:
        from agent import approvals as _appr
        return {"ok": True, "result": _appr.approve(write_id)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/staged-writes/{write_id}/reject")
def staged_writes_reject(write_id: str):
    try:
        from agent import approvals as _appr
        return {"ok": True, "result": _appr.reject(write_id)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/world-state")
def world_state_get():
    """Current world state synthesized by the world model."""
    try:
        from agent import world_model as _wm
        return {"state": _wm.get(), "prefs": __import__("agent.prefs", fromlist=["get"]).get()}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/perception")
def perception_query(q: str = "", hours: float = 24.0, limit: int = 50):
    """Query the persistent perception log."""
    try:
        from agent import perception as _perc
        if q:
            results = _perc.query(q, since_hours=hours, limit=limit)
        else:
            results = _perc.recent(since_hours=hours, limit=limit)
        return {"events": results}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/awareness/ingest")
async def awareness_ingest(request: Request):
    """Let the browser extension (or PWA) push web context into Apex's awareness."""
    body = await request.json()
    source = (body.get("source") or "web")[:32]
    content = (body.get("content") or "").strip()[:500]
    if not content:
        return JSONResponse({"error": "empty content"}, status_code=400)
    if _awareness_log is None:
        return JSONResponse({"error": "awareness not active"}, status_code=503)
    _awareness_log.add(source, content)
    return {"ok": True}


@app.get("/api/pair/qr")
def pair_qr(request: Request):
    """PNG QR code encoding the pairing URL (base + token) for the phone to scan."""
    if config.DASHBOARD_TOKEN and not _require_master(request):
        return JSONResponse({"error": "Only the master token can create a pairing QR."}, status_code=403)
    try:
        import qrcode
        import io
        img = qrcode.make(_pair_url(request))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return Response(buf.getvalue(), media_type="image/png",
                        headers={"Cache-Control": "no-store"})
    except Exception as e:
        return JSONResponse({"error": f"qr unavailable: {e}"}, status_code=500)


# --- Morning Briefing ---
@app.get("/api/briefing")
def briefing_get():
    briefing_mod.init_db()
    cfg = briefing_mod.get_config()
    # Find current task info
    task_info = None
    from agent import briefing as _bm
    for task in sched.list_tasks():
        if task.get("description", "").startswith(_bm._BRIEFING_MARKER):
            task_info = {"id": task["id"], "last_run": task.get("last_run"), "run_count": task.get("run_count", 0)}
            break
    return {**cfg, "task": task_info}


@app.post("/api/briefing")
async def briefing_post(request: Request):
    body = await request.json()
    allowed = {"enabled", "time", "timezone", "location", "news_topics"}
    updates = {k: v for k, v in body.items() if k in allowed}
    result = briefing_mod.reinstall(updates)
    return {"ok": True, "result": result}


@app.post("/api/briefing/run_now")
async def briefing_run_now():
    from agent import briefing as _bm
    cfg = _bm.get_config()
    prompt = _bm._build_prompt(cfg)
    # Remove the marker so it reads cleanly
    prompt = prompt.replace(_bm._BRIEFING_MARKER + "\n", "")
    if not _agent_ref:
        return JSONResponse({"error": "agent not ready"}, status_code=503)
    loop = asyncio.get_event_loop()
    loop.run_in_executor(
        None,
        lambda: _agent_ref.run(prompt, include_screenshot=False, channel_id="briefing:manual"),
    )
    return {"ok": True, "message": "Briefing running — check Live Feed for output."}


# --- Chat ---
@app.post("/api/chat")
async def chat_endpoint(request: Request):
    global _chat_lock
    if _chat_lock is None:
        _chat_lock = asyncio.Lock()

    body = await request.json()
    user_text = (body.get("message") or "").strip()
    chat_id = body.get("chat_id") or str(uuid.uuid4())[:8]

    if not user_text:
        return JSONResponse({"error": "empty message"}, status_code=400)
    if not _agent_ref:
        return JSONResponse({"error": "agent not ready"}, status_code=503)

    async with _chat_lock:
        streamer = ChatStreamer(chat_id)
        loop = asyncio.get_event_loop()
        try:
            response = await loop.run_in_executor(
                None,
                lambda: _agent_ref.run(user_text, include_screenshot=False, streamer=streamer, channel_id=f"dashboard:{chat_id}"),
            )
        except Exception as e:
            ws_manager.broadcast_threadsafe({"type": "chat_error", "error": str(e), "chat_id": chat_id})
            return JSONResponse({"error": str(e)}, status_code=500)

    # Capture the turn this exchange landed on so the dashboard can attach
    # feedback (👍/👎) to the right bubble.
    session_id = tel_mod._session_id
    turn_index = tel_mod.current_turn()
    ws_manager.broadcast_threadsafe({
        "type": "chat_done",
        "response": response,
        "chat_id": chat_id,
        "session_id": session_id,
        "turn_index": turn_index,
    })
    return {
        "ok": True,
        "response": response,
        "chat_id": chat_id,
        "session_id": session_id,
        "turn_index": turn_index,
    }


# --- Council: Claude / GPT / Gemini debate ---
@app.get("/api/council/roster")
async def council_roster():
    from agent import council
    return {"roster": council.roster(), "presets": council.preset_names()}


@app.post("/api/council")
async def council_endpoint(request: Request):
    body = await request.json()
    question = (body.get("question") or "").strip()
    rounds = max(0, min(3, int(body.get("rounds", 1))))
    panel = body.get("panel") or None
    preset = body.get("preset") or "general"
    if not question:
        return JSONResponse({"error": "empty question"}, status_code=400)

    from agent import council

    def _progress(msg: str):
        ws_manager.broadcast_threadsafe({"type": "council_progress", "message": msg})

    def _answer(round_no, label, text):
        ws_manager.broadcast_threadsafe(
            {"type": "council_answer", "round": round_no, "label": label, "text": text}
        )

    def _round_start(round_no, labels):
        ws_manager.broadcast_threadsafe(
            {"type": "council_round_start", "round": round_no, "members": labels}
        )

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: council.convene(
                question, rounds=rounds, panel=panel, preset=preset,
                on_progress=_progress, on_answer=_answer,
                on_round_start=_round_start,
            ),
        )
    except Exception as e:
        ws_manager.broadcast_threadsafe({"type": "council_error", "error": str(e)})
        return JSONResponse({"error": str(e)}, status_code=500)

    payload = {
        "question": result.question,
        "members": result.members,
        "final_answer": result.final_answer,
        "transcript": result.transcript,
        "confidence": result.confidence,
        "confidence_note": result.confidence_note,
        "disagreement": result.disagreement,
    }
    ws_manager.broadcast_threadsafe({"type": "council_done", **payload})
    return {"ok": True, **payload}


# --- Research: cited answers ---
@app.post("/api/research")
async def research_endpoint(request: Request):
    body = await request.json()
    query = (body.get("query") or "").strip()
    depth = body.get("depth") or "standard"
    # Prior [{query, answer}] turns — what makes a follow-up resolvable.
    history = body.get("history") or []
    if not query:
        return JSONResponse({"error": "empty query"}, status_code=400)

    from agent import answers

    def _event(phase: str, payload: dict):
        # Source cards render before the first answer token — showing which
        # pages are being read is most of the perceived speed.
        ws_manager.broadcast_threadsafe(
            {"type": f"research_{phase}", "phase": phase, **payload}
        )

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: answers.answer(query, depth=depth, on_event=_event,
                                   history=history),
        )
    except Exception as e:
        ws_manager.broadcast_threadsafe({"type": "research_error", "error": str(e)})
        return JSONResponse({"error": str(e)}, status_code=500)

    if result.get("error"):
        ws_manager.broadcast_threadsafe(
            {"type": "research_error", "error": result["error"]}
        )
    ws_manager.broadcast_threadsafe({"type": "research_result", **result})
    return {"ok": not result.get("error"), **result}


@app.post("/api/research/save")
async def research_save(request: Request):
    """Save a research thread to Documents, citations and all."""
    body = await request.json()
    turns = body.get("turns")
    if not turns:                                   # single-answer callers
        turns = [body] if body.get("answer") else []
    turns = [t for t in turns if t.get("answer")]
    if not turns:
        return JSONResponse({"error": "nothing to save"}, status_code=400)

    from agent import answers, documents

    parts = []
    for t in turns:
        parts.append(f"## {t.get('query') or 'Question'}\n")
        parts.append(answers.format_markdown(
            {"error": "", "answer": t.get("answer", ""), "sources": t.get("sources") or []}
        ))
        parts.append("")
    title = (turns[0].get("query") or "Research").strip()
    doc = documents.create(title=f"Research: {title}", content="\n".join(parts))
    return {"ok": True, "id": doc["id"]}


# --- Compare: blind side-by-side model testing (complements the council) ---
@app.get("/api/compare/roster")
async def compare_roster():
    from agent import council
    return {"roster": council.roster()}


@app.post("/api/compare/run")
async def compare_run(request: Request):
    body = await request.json()
    question = (body.get("question") or "").strip()
    panel = body.get("panel") or None
    if not question:
        return JSONResponse({"error": "empty question"}, status_code=400)
    from agent import compare
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: compare.run(question, panel=panel))
    code = 400 if result.get("error") else 200
    return JSONResponse(result, status_code=code)


@app.post("/api/compare/pick")
async def compare_pick(request: Request):
    body = await request.json()
    compare_id = body.get("compare_id") or ""
    slot = body.get("slot") or ""
    note = body.get("note") or ""
    from agent import compare
    result = compare.pick(compare_id, slot, note=note)
    return JSONResponse(result, status_code=400 if result.get("error") else 200)


@app.post("/api/compare/synthesize")
async def compare_synthesize(request: Request):
    body = await request.json()
    compare_id = body.get("compare_id") or ""
    from agent import compare
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: compare.synthesize(compare_id))
    return JSONResponse(result, status_code=400 if result.get("error") else 200)


@app.get("/api/compare/leaderboard")
async def compare_leaderboard():
    from agent import compare
    return compare.leaderboard()


@app.get("/api/learning")
def learning_stats(days: int = 30):
    """The learning loop's scoreboard: is reranking actually earning its cost?"""
    out = {}
    try:
        from agent import reranker
        out["rerank"] = reranker.stats(days=days)
    except Exception as e:
        out["rerank"] = {"error": str(e)}
    try:
        from agent import trajectory
        out["tools"] = trajectory.stats(days=days)
    except Exception as e:
        out["tools"] = {"error": str(e)}
    return out


# --- Documents: writing-first editor with AI edits ---
@app.get("/api/documents")
def documents_list():
    from agent import documents
    return {"documents": documents.list_documents()}


@app.get("/api/documents/{doc_id}")
def documents_get(doc_id: int):
    from agent import documents
    doc = documents.get(doc_id)
    return doc if doc else JSONResponse({"error": "not found"}, status_code=404)


@app.post("/api/documents")
async def documents_create(request: Request):
    body = await request.json()
    from agent import documents
    return documents.create(title=(body.get("title") or "Untitled"),
                            content=body.get("content") or "")


@app.put("/api/documents/{doc_id}")
async def documents_update(doc_id: int, request: Request):
    body = await request.json()
    from agent import documents
    doc = documents.update(doc_id, title=body.get("title"), content=body.get("content"))
    return doc if doc else JSONResponse({"error": "not found"}, status_code=404)


@app.delete("/api/documents/{doc_id}")
def documents_delete(doc_id: int):
    from agent import documents
    return {"ok": documents.delete(doc_id)}


@app.post("/api/documents/{doc_id}/to-vault")
def documents_to_vault(doc_id: int):
    from agent import documents
    res = documents.export_to_vault(doc_id)
    return JSONResponse(res, status_code=400 if res.get("error") else 200)


@app.post("/api/documents/ai-edit")
async def documents_ai_edit(request: Request):
    body = await request.json()
    from agent import documents
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: documents.ai_edit(
            text=body.get("text") or "",
            instruction=body.get("instruction") or "",
            preset=body.get("preset") or "",
        ),
    )
    return JSONResponse(result, status_code=400 if result.get("error") else 200)


# --- The Constellation: 12 domain-expert planets ---
@app.get("/api/constellation/roster")
async def constellation_roster():
    from agent import constellation
    return {
        "planets": constellation.list_planets(),
        "max_planets": getattr(config, "CONSTELLATION_MAX_PLANETS", 4),
        "auto": getattr(config, "CONSTELLATION_AUTO", False),
    }


@app.post("/api/constellation")
async def constellation_endpoint(request: Request):
    body = await request.json()
    question = (body.get("question") or "").strip()
    keys = body.get("planets") or None
    if not question:
        return JSONResponse({"error": "empty question"}, status_code=400)

    from agent import constellation

    def _progress(msg: str):
        ws_manager.broadcast_threadsafe({"type": "constellation_progress", "message": msg})

    def _answer(key, text):
        ws_manager.broadcast_threadsafe(
            {"type": "constellation_answer", "key": key, "text": text}
        )

    def _planet_start(roster):
        ws_manager.broadcast_threadsafe(
            {"type": "constellation_start", "planets": roster}
        )

    if keys:
        chosen = [constellation.PLANETS[k] for k in keys if k in constellation.PLANETS]
        planets = chosen or constellation.route(question, force=True)
    else:
        planets = constellation.route(question, force=True)

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: constellation.convene(
                question, planets=planets, on_progress=_progress,
                on_answer=_answer, on_planet_start=_planet_start,
            ),
        )
    except Exception as e:
        ws_manager.broadcast_threadsafe({"type": "constellation_error", "error": str(e)})
        return JSONResponse({"error": str(e)}, status_code=500)

    payload = {
        "question": result.question,
        "experts": [p["display"] for p in result.planets],
        "planets": result.planets,
        "final_answer": result.final_answer,
        "takes": result.takes,
        "confidence": result.confidence,
        "confidence_note": result.confidence_note,
        "disagreement": result.disagreement,
    }
    ws_manager.broadcast_threadsafe({"type": "constellation_done", **payload})
    return {"ok": True, **payload}


@app.post("/api/constellation/chat")
async def constellation_chat(request: Request):
    """Direct 1:1 conversation with a single expert planet."""
    body = await request.json()
    key = (body.get("planet") or "").strip()
    message = (body.get("message") or "").strip()
    history = body.get("history") or []
    if not key or not message:
        return JSONResponse({"error": "planet and message are required"}, status_code=400)

    from agent import constellation
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, lambda: constellation.chat_with_planet(key, message, history)
    )
    if result.get("error"):
        return JSONResponse(result, status_code=400)
    return result


# --- Voice: speech-to-text (OpenAI Whisper) ---
@app.post("/api/transcribe")
async def transcribe_endpoint(file: UploadFile = File(...)):
    if not config.OPENAI_API_KEY:
        return JSONResponse({"error": "OPENAI_API_KEY not set — voice input needs it"}, status_code=503)
    data = await file.read()
    if not data:
        return JSONResponse({"error": "empty audio"}, status_code=400)

    from openai import OpenAI
    client = OpenAI(api_key=config.OPENAI_API_KEY)
    name = file.filename or "speech.webm"
    loop = asyncio.get_event_loop()
    try:
        tr = await loop.run_in_executor(
            None,
            lambda: client.audio.transcriptions.create(model="whisper-1", file=(name, data)),
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return {"text": (getattr(tr, "text", "") or "").strip()}


# --- Voice: text-to-speech (OpenAI TTS) ---
@app.post("/api/speak")
async def speak_endpoint(request: Request):
    if not config.OPENAI_API_KEY:
        return JSONResponse({"error": "OPENAI_API_KEY not set"}, status_code=503)
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "empty text"}, status_code=400)

    from openai import OpenAI
    client = OpenAI(api_key=config.OPENAI_API_KEY)
    voice = getattr(config, "OPENAI_TTS_VOICE", "alloy")
    loop = asyncio.get_event_loop()
    try:
        audio = await loop.run_in_executor(
            None,
            lambda: client.audio.speech.create(model="tts-1", voice=voice, input=text[:4000]).content,
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return Response(content=audio, media_type="audio/mpeg")


# --- WebSocket live stream ---
@app.websocket("/ws/live")
async def ws_live(ws: WebSocket):
    # HTTP middleware does not run for WebSocket upgrades, so the dashboard
    # token must be checked here against the ?token= query parameter.
    token = config.DASHBOARD_TOKEN
    if token:
        qtoken = ws.query_params.get("token")
        ok = bool(qtoken) and hmac.compare_digest(qtoken, token)
        if not ok:
            try:
                from agent import access_tokens
                ok = access_tokens.verify(qtoken)
            except Exception:
                ok = False
        if not ok:
            await ws.close(code=1008)  # 1008 = policy violation
            return
    await ws_manager.connect(ws)
    if ws_manager.loop is None:
        ws_manager.loop = asyncio.get_event_loop()
    # Register the connecting device so the hub can route + the dashboard can list it.
    # Bookkeeping must never cost us the socket: this used to run unguarded, so a
    # single DB error here closed the connection before the first frame — killing
    # the live feed, research streaming and council streaming for that client,
    # with nothing on screen to say why.
    try:
        from agent import devices as _devices
        device_id = ws.query_params.get("device", "")
        if device_id:
            _devices.touch(
                device_id,
                label=ws.query_params.get("label", ""),
                kind=ws.query_params.get("kind", "web"),
                user_agent=ws.headers.get("user-agent", ""),
            )
    except Exception as e:
        print(f"[Dashboard] device registration failed (continuing): {e}")
    try:
        # Send initial snapshot
        await ws.send_json({"type": "snapshot", "ts": time.time(), "data": {
            "subagents": orchestrator.list_all(),
            "tasks": sched.list_tasks(),
            "events_recent": _awareness_log.recent(60) if _awareness_log else [],
        }})
        while True:
            await ws.receive_text()  # keep alive — also a heartbeat
            if device_id:
                _devices.touch(device_id)
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)


_START_TIME = time.time()


# ---------------------------------------------------------------------------
# Skill Curator
# ---------------------------------------------------------------------------
@app.get("/api/curator/status")
async def curator_status():
    try:
        from agent import curator as _curator
        return _curator.status()
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/curator/run")
async def curator_run(dry_run: bool = False):
    try:
        from agent import curator as _curator
        report = _curator.run(dry_run=dry_run, client=None)
        return {"report": report, "dry_run": dry_run}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/curator/rollback")
async def curator_rollback():
    try:
        from agent import curator as _curator
        return {"result": _curator.rollback()}
    except Exception as e:
        return {"error": str(e)}


# === Public API: start in a background thread ===
def start_in_background(port: int = 7860, host: str | None = None) -> threading.Thread:
    import uvicorn

    def runner():
        _host = host if host is not None else config.DASHBOARD_HOST
        # Fail closed: never expose a tokenless dashboard on a public interface.
        # (A tunnel like Cloudflare/Tailscale still reaches a loopback bind.)
        loopback = {"127.0.0.1", "localhost", "::1"}
        if _host not in loopback and not config.DASHBOARD_TOKEN:
            print("[Dashboard] ⚠ Refusing to bind " + _host + " with an empty "
                  "DASHBOARD_TOKEN — anyone could run commands. Falling back to "
                  "127.0.0.1. Set DASHBOARD_TOKEN to expose Apex.")
            _host = "127.0.0.1"
        cfg = uvicorn.Config(app, host=_host, port=port, log_level="warning")
        server = uvicorn.Server(cfg)
        server.run()

    t = threading.Thread(target=runner, daemon=True, name="DashboardServer")
    t.start()
    return t
