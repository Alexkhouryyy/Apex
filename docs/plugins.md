# Executable Apex plugins

Plugins extend the running agent with Python tools, tool lifecycle hooks, slash commands,
bundled instruction skills, and selectable memory/context providers. They are separate
from standalone SKILL.md imports and MCP connections.

## Install and manage

Open **Plugins** in the dashboard. Paste a public GitHub repository URL, choose a branch,
tag or commit, and optionally a plugin subdirectory. Review downloads the package at an
immutable commit and shows its manifest, files and dependency/compatibility issues. View
source files before installing. **Install disabled** does not execute any plugin code.

Save the plugin's settings, check the trust box and enable it. Enabling imports Python
and calls `register(ctx)`. Tools are offered to Apex on its next model request; dispatch
also checks activation so a remembered tool name cannot call a disabled plugin. Voice
uses the same tool registry in Work mode. Discuss and Observe do not gain executable
plugin tools or slash commands.

**Check update** reviews the source ref again, showing its new commit and files. Applying
an update disables the plugin until you explicitly enable the new revision. Five earlier
packages are retained for rollback. Rollback also disables the restored version. An
update never silently changes code that remains enabled. Remove unregisters the plugin;
package snapshots and plugin-owned data are retained. Disabling prevents new dispatches;
already-running calls or plugin-created background threads may require an Apex restart.

The bundled **Session Insights** plugin provides real current-process tool counts and
latency, a `usage` tool and `/session-insights:status`. It retains no tool inputs/results.
It is opt-in and resets its in-memory counters when reloaded or restarted.

## Package contract

```text
my-plugin/
  plugin.yaml
  __init__.py
  helper.py                 # relative imports work
  skills/<name>/SKILL.md     # optional; exposed through a read tool
```

```yaml
name: my-plugin
version: 1.0.0
description: Describe the actual capability
provides_tools: [transform]
config_schema:
  prefix: {type: str, default: "Result: "}
```

```python
def register(ctx):
    def transform(args):
        return ctx.get_config("prefix") + args["text"].upper()

    ctx.register_tool(
        name="transform", handler=transform,
        schema={"description": "Uppercase supplied text", "parameters": {
            "type": "object", "properties": {"text": {"type": "string"}},
            "required": ["text"], "additionalProperties": False,
        }},
    )
    ctx.register_command("status", lambda args: "Ready", "Show plugin status")
```

Names are namespaced in the model registry; plugins cannot shadow core or other plugin
tools. Commands use `/my-plugin:status arguments`. JSON Schema validates tool inputs
before invoking the handler. Sync and async tools/commands are supported. The context
exposes `plugin_dir`, `data_dir`, `logger`, `config`, `get_config(key, default)`, and
`dispatch_tool(name, args)` through Apex's existing scope/safety checks.

Supported hooks:

| Hook | Keyword arguments | Behavior |
| --- | --- | --- |
| `pre_tool_call` | `tool_name`, `args`, `task_id` | Runs after Apex policy checks; `block` or `approve` directives refuse the call with a message. It cannot override a refusal. |
| `post_tool_call` | `tool_name`, `args`, `result`, `task_id`, `duration_ms` | Observes the result, including refusals; return value ignored. |

Hooks receive copies of payloads. A failing pre-hook blocks the call; a failing
post-hook is recorded without interrupting the completed tool. Calls dispatched from a
hook do not recursively emit more plugin hooks. Hooks should accept `**kwargs`.

## Runtime providers

The dashboard selects one memory provider and one context engine, each defaulting to
Apex built-in. These are **Apex contracts**, not Hermes provider class compatibility:

```python
ctx.register_memory_provider("notes", recall=recall, remember=remember)
ctx.register_context_engine("compact", summarize=summarize)
```

`remember(content, kind, importance, tags)` returns a confirmation string.
`recall(query, limit, kind, semantic)` returns dictionaries containing string `content`
fields; callers may also use `id`, `kind`, `importance`, `tags`, and `ts`.
These handlers serve the `longterm.remember/recall` routes, including conversation tools.
Apex's separate identity/MEMORY.md/USER.md files, memory editing UI, database search and
project continuity remain built-in. Existing records are not migrated when switching.

`summarize(messages, summary)` receives older conversation messages and the existing
rolling summary. It returns a nonempty summary up to 20,000 characters. Apex retains the
recent 12 messages verbatim. A provider error preserves the full conversation. Providers
are synchronous. A provider can delegate to the built-in memory functions without recursion.
An unavailable selected memory provider reports failure rather than silently saving to a
different store. Disabling/removing/updating it explicitly resets its selection to built-in.

## Compatibility and dependencies

This implements a useful subset of Hermes's native `plugin.yaml` + `register(ctx)`
conventions. A plugin using only the documented tools, commands and hooks above can work
unchanged. This is not full Hermes compatibility. Hermes-specific imports, memory/context
provider classes, model providers, channel adapters, CLI subcommands, dashboard/desktop
extensions, LLM host APIs, other lifecycle hooks, plugin packs, portable `plugin.json`
packages and privileged override capabilities need adapters or additional runtime support.
Unsupported declared hooks/capabilities/dependencies are reported before activation;
unsupported Python API calls fail at registration without publishing partial tools.

Dependencies are checked against Apex's interpreter using PEP 508 requirements from
`pyproject.toml` or `python_dependencies`. Missing/conflicting packages block activation.
Apex does not run pip or repository setup scripts automatically. Secrets use `requires_env`
and existing environment setup; their values are never part of the plugin inventory.
Inline secret settings are unsupported. Private repositories and archives larger than
25 MB compressed / 50 MB selected files need a different delivery path.

Native plugins are trusted host code, **not sandboxed**. The trust checkbox grants code
execution, not fine-grained filesystem/network isolation. Manifest checks and hashes
catch accidental edits and unsupported declarations; they do not establish that code is safe.
Only reviewed packages execute after enablement. External plugin installation does not
merge source into Apex's Git checkout or modify the model's weights.

## Verification

`tests/test_plugins.py` loads real temporary Python plugins and checks activation,
dispatch, schema validation, import failures, hooks, provider routing, version replacement,
rollback, dependency reporting, file confinement and API access controls. The dashboard
workflow in `scripts/check_environment_ui.cjs` uses mocked HTTP responses; it does not
download or enable external plugins. Third-party compatibility must be checked per package.

Reference: https://hermes-agent.nousresearch.com/docs/developer-guide/plugins
