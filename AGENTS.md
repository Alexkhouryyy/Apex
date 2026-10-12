# Optional REA analysis

Use normal source searches and project checks for everyday Apex development.
Use REA when the task asks for evidence from a shipped application, binary,
package, or application behavior. Read
`.agents/skills/reverse-engineer-anything/SKILL.md` for that workflow.

If the current Codex client exposes REA MCP tools, use their actual schemas.
In a hosted cloud task without those tools, prefer the project-local CLI:

```bash
node integrations/rea/node_modules/rea-agents/scripts/rea.mjs --help
```

If that dependency is absent and installing REA is within the task's scope,
run `bash scripts/setup_rea_cloud.sh` when registry access is available. The
cloud environment setup phase can preinstall it for tasks without agent-phase
internet access. This project-local installation does not require changing
the cloud host's personal Codex configuration.

The integration pins REA 6.4.0. Separate observed evidence from inference and
retain the tool's coverage limitations. Follow `docs/REA-CODEX.md` for local
Codex registration, Apex acceptance and cloud setup.
