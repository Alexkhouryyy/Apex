# Apex

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![Tests](https://img.shields.io/badge/tests-1616%20passing-brightgreen)

**A self-hosted, voice-first, always-on personal AI agent.** Runs on your own
hardware. Model-agnostic — Claude, GPT, Gemini, or a local Ollama model, no
lock-in. One persistent "brain" that every device and channel is a window
into, not a chat log that forgets you between sessions.

Full write-up, feature map and honest state: **[docs/APEX_OVERVIEW.md](docs/APEX_OVERVIEW.md)**.

## What it does

- **Talks, sees, and acts** — voice or text, reads your screen, controls your
  computer (clicks, keystrokes, browser automation), runs shell commands and a
  persistent Python REPL, behind a dangerous-command safety gate.
- **Remembers everything, in one place** — a single SQLite brain (memory,
  goals, skills, telemetry) that every device is a window into, with semantic
  search and a knowledge graph over your own files.
- **Acts on its own, with a governor** — a background loop reads goals and
  context on a timer and proposes the next action; safe steps run
  automatically, risky ones stage for your approval first. Nothing on your
  computer happens without that gate.
- **Reaches you everywhere** — Telegram, Discord, Slack, WhatsApp, Signal, and
  phone/SMS via Twilio, plus an installable PWA dashboard with web push, all
  driven by the same brain.
- **A multi-model council** — Claude, GPT and Gemini debate a question to a
  synthesized answer; a blind side-by-side comparison mode tracks which model
  actually wins your preference over time.
- **Sees your hands** — a native webcam hand tracker (no browser, no
  third-party service) drives a 3D glass board on the dashboard: pinch to
  drag, two hands to scale and rotate cards and real 3D models.
- **Builds what you ask for** — "Apex, create a red cube, 50 millimetres
  wide" creates a real, measured object in Blender and puts it on the board,
  through a restricted command set with no arbitrary code execution.
- **Operable from itself** — a Control tab edits settings, shows MCP server
  health, switches themes, checks for and pulls updates, and restarts Apex —
  no terminal needed after first setup.
- **Runs on your subscription** — the conversation can route through your
  existing Claude subscription instead of metered API credits, with an
  automatic fallback and honest cost reporting.
- **Self-improving** — writes its own tools on a capability gap, consolidates
  what worked in a nightly reflection pass, and rolls back changes that
  measurably hurt approval rates.

## Quickstart

```bash
pip install -r requirements.txt
cp .env.example .env          # then fill in your keys
python main.py --text         # text mode (no mic/speakers needed)
```

`python main.py` alone runs full voice mode. The web dashboard starts
automatically — watch the console for `[Dashboard] http://127.0.0.1:7860`.

### Keys

Only `ANTHROPIC_API_KEY` is required. Add `OPENAI_API_KEY` and/or
`GEMINI_API_KEY` to unlock GPT/Gemini models and the 3-way council. All keys
live in `.env`, which is gitignored — see **Security** below.

### Switching models

- At launch: `python main.py --text --model gpt-4o`
- At runtime: type `/model gemini-2.5-flash` (or `/model` to list options)
- Council debate: type `/council <your question>`

### Optional features

Off by default — each needs one `.env` flag and, for the hardware-facing
ones, something running alongside Apex:

| Feature | Flag | Needs |
|---|---|---|
| Hand tracking + 3D glass board | `HANDTRACK_ENABLED=true`, `BOARD_ENABLED=true` | A webcam. Open `/board` on the dashboard. |
| Voice-driven 3D creation | `BLENDER_ENABLED=true` | Blender running with `blender/apex_blender_addon.py` installed — see [blender/README.md](blender/README.md). |
| Run on your Claude subscription | `SUBSCRIPTION_ENABLED=true` | The `claude` CLI installed and logged in. |
| MCP tool servers | — | `mcpServers` in `mcp_servers.json` or `~/.claude/settings.json`; check status on the dashboard's **Control** tab. |

## Remote Access (Tailscale)

Use the dashboard from your phone anywhere — no shared Wi-Fi required.
[Tailscale](https://tailscale.com) is a private WireGuard mesh that links only
your own devices; it is safer than exposing the dashboard to the public
internet.

**1. Install Tailscale** on the host computer and on your phone, and sign both
into the **same account**.

**2. Get the host's Tailscale IP** — on the computer:
```bash
tailscale ip -4        # e.g. 100.x.y.z
```

**3. Configure Apex** — in `.env`:
```
DASHBOARD_HOST=0.0.0.0
DASHBOARD_TOKEN=pick-a-strong-password
```
`DASHBOARD_TOKEN` is your backstop password — always set it when binding to
`0.0.0.0`. Start Apex.

**4. Open it on your phone's browser:**
```
http://100.x.y.z:7860
```

**Nicer — HTTPS with a name instead of an IP.** On the host run:
```bash
tailscale serve 7860
```
Then open `https://<machine-name>.<your-tailnet>.ts.net` (MagicDNS, on by
default). No port, no IP, proper TLS.

> The host computer must stay on and running Apex. Tailscale connects your
> devices — it does not keep Apex alive.

## Security

API keys never belong in the repo. They live only in `.env`, which is
gitignored. `.env.example` (placeholders only) is the template.

### Pre-commit secret guard

A version-controlled hook blocks commits that contain a key or an env file.
Activate it once per clone:

```bash
git config core.hooksPath .githooks
```

It rejects staged `.env` / `*.key` / `*.pem` files and scans added lines for
live key patterns. Bypass a verified false positive with
`git commit --no-verify`. For a heavier industry-standard scanner, install
[`gitleaks`](https://github.com/gitleaks/gitleaks) and run
`gitleaks protect --staged`.

### Before making this repo public

1. Scan all history: `gitleaks detect` (or `trufflehog git file://.`) — expect 0 leaks.
2. Confirm no tracked secrets: `git ls-files | grep -E '\.env$|\.db$'` should be empty.
3. Enable GitHub **Secret Scanning + Push Protection** (Settings → Code security) — free on public repos.
4. **Rotate every API key** and update `.env`. Rotation is the only true fix for any key that was ever pasted, screenshotted, or shared.
5. Verify `.env.example` still holds only placeholders.
