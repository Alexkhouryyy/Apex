# Apex task workspace

Open the dashboard, select **Constellation**, and use **Task workspace**.
Enter a concrete task and the project folder, constraints and acceptance checks.
Select only the specialists needed. Model fields accept provider model IDs;
blank fields use `AGENT_MODEL` from startup. DeepSeek Flash is supported as
`deepseek-flash`. Credentials remain in Apex's existing settings.

Research → coding → independent review → Apex summary is an ordered workflow.
Research and review can inspect files and retrieve relevant stored memories.
Coding can edit files and run shell commands through Apex's existing safety
checks. Review is read-only: it can inspect implementation and reported test
output but cannot execute its own tests. Apex receives the recorded handoffs
and summarizes them. Each specialist has its own fresh model history; user
project context and preceding results are passed explicitly.

This is a task panel inside the constellation, not animated rooms, automatic
planning or parallel coding agents. It does not launch Codex/OpenCode/Claude
CLI sessions. Existing advisory planets and their journals still work separately.

You can also ask Apex in text chat or companion Work mode to use
`start_team_task`, then ask for progress through `team_task_status`. Chat-launched
tasks inherit the currently selected conversation model unless explicitly overridden.
`stop_team_task` requests cancellation. Discuss mode can read task status but
cannot start or stop work. Task creation from chat requires the dashboard running.

## First useful task

Use a small project change with an objective check, for example:

> In the project folder below, locate the settings validation code. Add validation
> for an empty project name and run the relevant existing tests. Review the change
> for regressions. Report the changed paths and the actual test results.

Supply the actual project path and intended behavior. Do not run two writers
against that same project at once. The team endpoint allows one active team run;
it does not lock out your editor or other Apex interfaces.

## Status, evidence and limits

- Expand a task to see each stage's model, state, text, tool results, calls and
  estimated model cost. Tool results are bounded previews, not full terminal logs.
- `done` means the selected stages returned complete answers. Read the review
  and summary to judge whether the requested outcome succeeded.
- A denied tool, missing usage, provider error, cancellation or execution limit
  stops the workflow and skips later stages. Partial evidence remains visible.
- Maximum four model calls per stage, sixteen per task, and 24 tools per task.
- Task budget defaults to $0.50; daily/session Apex budgets also apply. Prices
  come from `MODEL_PRICING`; unpriced paid models are rejected. Local Ollama
  inference is counted as $0 API spend. Model cost excludes tool infrastructure
  and any external services invoked by host commands.
- Budget checks happen between calls. One in-flight call can exceed the limit;
  this is not a prepaid reservation or a hard dollar ceiling.
- Web search in a team uses direct DDGS search rather than a hidden nested
  Anthropic model call.
- Stop takes effect when the current synchronous call/tool returns. It cannot
  undo completed actions or terminate an already running shell process.
- Runs and their evidence persist in SQLite alongside Apex memory. Refreshing or
  disconnecting does not restart work. Resubmitting the same task ID is idempotent.
  After a host restart, unfinished runs become `interrupted` and are never replayed.
- Results are available in this task panel. They are not automatically promoted
  into durable personal facts or copied into the main chat history.

## Verification

Offline tests cover file-change handoff to independent inspection, tool allowlists,
model choice, durable state, duplicate submission, restart interruption, task limits,
budget stops, cancellation, failure propagation, authentication and origin checks.
The DOM simulation checks task submission, reconnect recovery, result escaping
and stop controls. These checks do not substitute for a live provider/Windows test.
