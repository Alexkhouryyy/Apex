# DeepSeek V4.1 Flash

Official model ID: `deepseek-flash`. Uses the existing OpenAI-compatible
DeepSeek adapter with text, images, streaming and tool calls.

On Windows, from the repository:

```bat
.venv\Scripts\python.exe scripts\setup_deepseek.py
.venv\Scripts\python.exe main.py --text
```

The setup prompts for a hidden API key and saves the startup default to the
project `.env`, backing up existing settings. It does not change memory paths.
It also migrates the automatic reasoning model settings to DeepSeek, including
the proactive model, Guardian, Time Capsule, Constellation helpers and command
reviewer. Use `scripts/setup_deepseek.py --use-existing-key` to reuse the key
already saved in this project's `.env` without prompting or displaying it.
Environment variables already set in the terminal take precedence over `.env`.

Use `/model deepseek-flash` or `/model MODEL_ID` at the Apex text prompt,
or the dashboard model selector. Runtime selection lasts for that process.
Use `scripts/set_env_key.py AGENT_MODEL MODEL_ID` to change the next-start default.
Other providers need their own credentials. A missing key leaves the current
selection unchanged. Selecting DeepSeek never routes the main turn through the
Claude subscription adapter.

Flash uses non-thinking mode explicitly. The portable adapter does not preserve
DeepSeek reasoning history, which its thinking-mode tool calls require. This
does not remove ordinary chat, vision or tool use. Extended thinking integration
is a separate task. Cost telemetry uses conservative peak estimates.

Automatic reasoning now follows the startup brain through `BACKGROUND_MODEL`
(defaults to `AGENT_MODEL`). WorldModel, Reflection, memory compression,
awareness, Cortex, skill generation, preferences, goals, curation, screen
description and deep research select the matching provider rather than a stale
Anthropic client. Default Guardian, Time Capsule and Constellation helper models
also follow the non-Anthropic background brain. Web search uses DDGS directly
in non-Anthropic mode; it does not send Anthropic's server-side search tool to
DeepSeek. Explicit specialist/reviewer/council model overrides remain explicit.

Manual `/model` switching changes the conversation immediately. Background jobs
keep the startup configuration until restart, so an in-flight job has a stable
provider. Set `AGENT_MODEL=deepseek-flash` and `BACKGROUND_MODEL=deepseek-flash`
in `.env` and restart to move both. Remove unwanted explicit overrides such as
`PROACTIVE_MODEL`, `GUARDIAN_MODELS`, `CONSTELLATION_PLANET_MODEL`,
`CONSTELLATION_MEMORY_MODEL`, `CONSTELLATION_SYNTH_MODEL`, `TIME_CAPSULE_MODEL`
or `SAFETY_REVIEW_MODEL` if they still name a provider you do not want to use.
The mistaken name `deepseek-v4.1-flash` is normalized to `deepseek-flash`.
Speech services and explicitly selected multi-provider councils retain their
own provider credentials; this is not a conversion of speech models.
Live API and Windows hardware validation require the user's configured machine.

Sources checked 2026-09-14:
- https://api-docs.deepseek.com/quick_start/pricing/
- https://api-docs.deepseek.com/guides/thinking_mode/
