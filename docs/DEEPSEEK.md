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

This switches the main conversational agent. Existing specialist/background
features and speech services may still require their configured providers,
including Anthropic or OpenAI; this is not a conversion of every subsystem.
Live API and Windows hardware validation require the user's configured machine.

Sources checked 2026-09-14:
- https://api-docs.deepseek.com/quick_start/pricing/
- https://api-docs.deepseek.com/guides/thinking_mode/
