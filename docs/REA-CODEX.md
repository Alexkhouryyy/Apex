# REA in Codex, Apex and cloud tasks

[REA](https://github.com/morluto/rea) provides tools for inspecting shipped
applications, JavaScript/Electron artifacts, .NET assemblies and native
binaries. Codex uses the resulting evidence to explain or implement a feature.
Use ordinary repository tools for normal source-code work. REA is for questions
that need artifact or runtime evidence.

This integration pins the published `rea-agents` **6.4.0** package and its
matching workflow instructions. Newer repository documentation can describe
features absent from this release.

## Windows Codex

In Windows PowerShell:

```powershell
node --version
npx -y rea-agents@6.4.0 setup --client codex --dry-run --json
npx -y rea-agents@6.4.0 setup --client codex --yes --json
codex mcp get rea
npx -y rea-agents@6.4.0 doctor --client codex --json
```

REA requires Node 22.x at least 22.19, Node 24.x at least 24.11, or Node 26+.
Node 24.14 satisfies this requirement. The scoped setup registers only Codex
and installs matching personal instructions. It backs up and preserves existing
configuration. On Windows it generates the `cmd.exe /d /c` runner needed to
launch npm's command shim. Restart Codex after setup.

The dry run shows the actual configuration and skill destinations, including
any existing `CODEX_HOME` override. `--yes` applies that explicitly selected
client scope; it does not install Hopper or another analysis engine.

MCP registration does not sign into ChatGPT. If Codex is not signed in, run
`codex login` and select **Sign in with ChatGPT** before starting a coding task.

## Codex inside Apex

Apex Code runs the official Codex program with the user's environment and MCP
configuration. Its command adds the Apex memory server without replacing
`mcp_servers.rea`. The Windows setup above therefore also makes REA available
to the **ChatGPT / Codex** coding engine in Apex.

Restart Apex after setup, select the ChatGPT plan in `/code`, and open a new
workspace. Ask it to list REA's available tools and inspect a small local
JavaScript application artifact. Confirm that the feed shows the REA tool call
and its evidence before using it on a larger target. A resumed conversation
can retain earlier tool state; start a new workspace for this acceptance check.

This integration concerns Apex's Codex engine. Claude Code has separate,
restricted MCP configuration in Apex and is not configured by these commands.

## Codex cloud tasks

The project contains the released REA skill under
`.agents/skills/reverse-engineer-anything`, plus locked dependencies under
`integrations/rea`. Add this command to the repository's cloud environment
setup script, after provisioning a supported Node version:

```bash
bash scripts/setup_rea_cloud.sh
```

The setup installs dependencies in the project and verifies a real MCP
handshake, tool discovery and static analysis. It previews Codex registration
without writing the cloud host's personal configuration or installing an
analysis engine. Keep existing setup commands and append this command.

Cloud agents can use the installed CLI through their shell tools:

```bash
node integrations/rea/node_modules/rea-agents/scripts/rea.mjs --help
node integrations/rea/node_modules/rea-agents/scripts/rea.mjs analyze-javascript-application /absolute/path/to/artifact --json
```

Installing a package in a cloud task does not add a new hosted MCP connector
to an already-running chat. The verified cloud integration uses the CLI; local
Codex uses its MCP registration. Preinstalling in the setup phase also makes
the CLI available when the agent phase has no npm registry access.

Dependencies are Git-ignored, so a new Apex worktree needs its own cloud setup
if you intend to use this project-local CLI there. The personal Windows MCP
registration launches the versioned npm package independently of worktree
dependencies.

## Verification and optional engines

Run `npm run check --prefix integrations/rea` after installing dependencies.
The probe uses a fixture that throws if executed, confirms the recovered import
graph, checks the input files remain unchanged, and verifies that the vendored
skill matches the installed release. The REA workflow runs this on Windows and
Linux. It does not call a model API or authenticate a user's coding plan.

JavaScript/Electron and static .NET inspection need no native decompiler.
Native decompilation requires an appropriate existing Ghidra, IDA or Hopper
installation. Choose an engine when a task needs it; Windows Ghidra support is
experimental in this release. See the upstream installation and target guides
for platform-specific capabilities.

See [REA installation](https://github.com/morluto/rea/blob/main/docs/installation.md)
and [Codex MCP configuration](https://developers.openai.com/codex/mcp/).
