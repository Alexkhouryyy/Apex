# Apex environment and capability management

The dashboard sidebar exposes Skills, Plugins, Repositories, MCP, Models, Channels & webhooks, Pairing, Sessions, Logs, System & updates, and Documentation. Settings & keys retains the existing configuration editor. Profiles and project context remain in Apex Home; Files & documents opens the document workspace.

## Add a repository

Paste or drop a public GitHub repository URL in Repositories. Select a branch/tag or leave HEAD. Apex resolves an immutable commit, saves its index and README, and discovers SKILL.md bundles and runtime manifests. This does not clone, execute or install the whole repository.

Preview a discovered skill. All supporting documentation, unsupported files and the upstream license are shown. Documentation-only bundles can be installed under a new name after recording compatibility notes. Imported bundles retain provenance and file hashes; edits invalidate availability. Plugins lists these bundles with enable/disable controls alongside actual MCP connection states.

Arbitrary Hermes/OpenClaw runtime plugins are not interchangeable with Apex plugins. Executable repositories need an adapter or an MCP server; the intake reports this rather than claiming they are installed. Private repositories, missing root licenses, oversized indexes and unsupported executable bundles are reported explicitly.

## Learn during conversation

Ask Celine to learn a reusable capability, or improve a named executable skill. The main agent has `develop_skill`, `repository_inspect`, `create_skill` and `skill_manage`. It checks existing capabilities first. Instruction-only procedures use skill_manage. Generated executable tools go through validation and staging in Approvals, including direct conversational create_skill calls.

The Skills page uses the same development backend. Offline tools require Docker validation; network tools receive syntax checks without executing module-level code. A failed proposal leaves the current skill intact. Approved executable rewrites retain the existing snapshot/rollback workflow. This is skill/tool development, not model-weight training.

## Operations

System & updates reuses Apex's existing checkout and supervisor controls. Updates require a clean checkout and use fast-forward-only pulls. Restart availability follows the supervisor. Channels reports configuration, not successful authentication. App account authorization stays in Apps.

## Validation

Run the focused Python suites including tests/test_environment.py. Browser workflow checks use scripts/check_environment_ui.cjs with Playwright and an installed Chromium; CHROMIUM_EXECUTABLE may select the local browser. Browser checks stub API responses and do not install external repositories or contact model providers.
