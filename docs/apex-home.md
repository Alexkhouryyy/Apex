# Apex Home: continuity before account connections

Open **Apex Home** from the dashboard sidebar after restarting Apex. The `/home`
page uses the existing owner dashboard token. Composio keys and app sign-ins stay
in **Apps**; this foundation does not enable additional accounts.

## What works

- **Identity:** editable display name, address, tone, detail and working preferences.
  Changes apply on the next local text, voice or companion turn. Celine remains
  an explicitly selected voice character and receives the same owner preferences.
  The legacy `JARVIS_PERSONA_ENABLED` switch still controls the default persona.
- **Projects:** the existing Spatial workspaces own the project brief, decisions,
  artifact references and next step. Voice follows the active workspace; saved chat
  threads remain bound to their original workspace. A turn pins its context so a
  concurrent workspace switch cannot redirect its checkpoint. Concurrent edits
  return a conflict instead of overwriting a newer revision.
- **Restart continuity:** dashboard chat uses its durable thread ID and restores
  recent text. Main voice/text conversations have separate persisted transcripts
  per project. The latest 30 text messages are restored; tool calls and screen
  frames are not replayed. The `project_checkpoint` tool saves a concise handoff.
- **Learning:** owner corrections can be added, revised and retired per project.
  Up to 20 active/inactive entries are stored and active entries reach future
  local turns. Existing evidence-based lessons remain in place. A team task with
  passed completion contracts can become an owner-written procedure proposal in
  Approvals. Automatic code proposals are also staged, never silently installed.
- **Task recovery:** interrupted team runs preserve their receipts. Every action
  with an uncertain outcome needs an explicit reconciliation note. The owner
  specifies remaining work and a new spending cap, then starts a linked task.
  Repeating the same continuation request returns the same child task. This is
  reviewed continuation, not automatic replay or an exactly-once execution claim.
- **Skill review:** preview an immutable OpenClaw or Hermes revision; inspect all
  instructions, support files and licenses; record compatibility requirements;
  then install or disable the reviewed skill. Changes to reviewed content disable
  its availability until reviewed again. Scripts/binaries require adaptation and
  are blocked by this documentation-only importer. It never installs dependencies.

## Reviewed starters

The next startup copies these complete packages into the existing procedural
skill library, preserving any installed package of the same name and its state:

| Apex skill | Upstream revision | Scope and prerequisites |
|---|---|---|
| `openclaw-github` | `openclaw/openclaw@8777448a9ffab1d0a5ad39d5abc11640cba849de`, `skills/github` | Existing `bash` tool and GitHub CLI. CLI found on this host; authentication is checked when GitHub work is requested. No automatic login, merge or publication. |
| `hermes-document-actions` | `NousResearch/hermes-agent@6e69a8933adda7dbbff7cf3009a259a4524477e9`, `skills/productivity/document-to-action-items` | Existing text/document tools turn sourced material into proposed actions. PDF/DOCX/OCR extraction remains a separate prerequisite. External tracker/calendar writes need a connected account and authorized scope. |

Original `SKILL.md` content, MIT notices and an Apex compatibility manifest are
retained with each starter. This is selective reuse; neither external agent
runtime is merged into Apex. Hermes's `grounded-citations` was inspected but not
installed because it depends on scripts and Hermes-specific state paths.

Sources: [OpenClaw GitHub workflow](https://github.com/openclaw/openclaw/blob/8777448a9ffab1d0a5ad39d5abc11640cba849de/skills/github/SKILL.md),
[Hermes document workflow](https://github.com/NousResearch/hermes-agent/blob/6e69a8933adda7dbbff7cf3009a259a4524477e9/skills/productivity/document-to-action-items/SKILL.md).

## Acceptance walkthrough

1. Save an address/tone preference in Identity. Start a new chat and companion
   turn; both receive the current preference.
2. Create a workspace, make it active and save a decision and next step. Restart
   Apex; reopen the conversation or use voice. The handoff is retained.
3. Switch Spatial workspaces while a previous conversation is open. Its next
   checkpoint still targets its original workspace; new conversations use the new
   active workspace. Voice keeps separate text history for each project.
4. Add a correction, use the project again, then retire it. It disappears from
   subsequent project prompt context.
5. Stop a team task. Review actual output state, resolve every uncertain action,
   then specify only remaining work. Inspect the new task's parent receipt link.
6. Read and disable one starter in Skills. It disappears from the agent's skill
   list; enabling it restores availability if its reviewed files are intact.

## Validation and remaining setup

Automated checks cover persistence, project binding, stale edits, channel
isolation, voice restoration, owner/origin restrictions, reviewed continuation,
skill provenance, support files, Windows newline compatibility, tamper detection
and proposal approval. The browser walkthrough uses an isolated database and
mock execution/provider data, including a 390px mobile viewport. Official GitHub
API fetches additionally validated the real pinned source bundles.

Live model behavior, microphone/voice output and real account actions are not
certified by these tests. Next: open Apps, enter the Composio project key there,
authorize desired accounts, then verify representative read and explicitly
authorized write actions. A visible catalog entry is not an authorized account.
