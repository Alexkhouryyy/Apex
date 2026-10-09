# Apex Code and Codex comparison

## Evidence and limits

This change was evaluated on 2026-10-09 against Apex after PR #50 and the
installed official Codex CLI 0.159.2, signed in through ChatGPT. It implements
gaps found in Apex's source, parser fixtures and DOM checks. It is not a completed
desktop usability study or a build-quality benchmark.

The same offline checklist prompt was submitted to Codex directly and through
Apex's coding runner in separate disposable Git projects. The direct Codex
process produced no output or files before a 180-second timeout. Apex's runner
reported failure after its 30-second attempt, with no reported usage or files.
These unequal timeout budgets and failed attempts cannot rank speed or quality.
No successful sample build is claimed.

Computer use was attempted with the available cloud browser. It could not reach
the workspace's loopback Apex server (`ERR_CONNECTION_REFUSED`) and had no
connection to the owner's PC. No live Apex or Codex desktop interaction was
observed. Account limits/activity were also unavailable from the local read-only
probe. Values in tests are fixtures, not live subscription measurements.

The supported Codex interfaces were checked against the official
[app-server documentation](https://developers.openai.com/codex/app-server).

## Implemented changes

| Area | Apex behavior before this change | Behavior in this change | Evidence |
| --- | --- | --- | --- |
| Token usage | One scalar; absent usage could appear as zero | Reported input, output, cache and reasoning fields; missing values remain unknown | Parser and session tests |
| Session totals | Review usage discarded; no completeness label | Coding and review totals, separate subtotals, duplicate-run protection, partial labels | Persisted-event tests and DOM fixtures |
| Account quota | No Code usage view | Owner-only read-only Codex account limits/activity, 60-second cache and refresh; explicit unavailable state | Fake public RPC protocol and endpoint authorization tests |
| Model visibility | Picker showed requested choice | Requested model/effort separate from reported model; run status visible | DOM checks |
| Busy composer | Submitting a draft could call Stop | Dedicated Stop; busy submissions preserve drafts | DOM checks of API calls |
| Command copy | Display label shortened the copied command | Copy uses bounded, redacted full command; historical commands retain their title | Parser and clipboard checks |
| Connected tools | Generic results could disappear | Result paired with its tool, success/failure and output retained; application errors fail | Parser and DOM checks |
| Changed files | Clickable text | Native button supports keyboard activation | DOM checks |

These are source-backed Apex improvements inspired by Codex's supported
interfaces. The table does not claim that every Codex interface or interaction
has been compared.

## Remaining work requiring a signed-in desktop session

Run both products from the same machine, with the same account, coding model,
effort, clean project contents and timeout budget. Use separate project copies.
Record requested/reported model, generated files, checks, elapsed time, reported
usage and any unavailable fields. Test adding a follow-up draft during work,
explicit Stop, long-command copying, connected-tool failures, reopening sessions,
review totals, account quota refresh and keyboard access to changed files.

Use this identical prompt for the creation task:

> Build a polished offline task checklist in one index.html, with no external
> dependencies. Use a dark theme with teal accents. Let me add tasks, mark them
> complete, filter All/Active/Done, delete a task, and persist tasks with
> localStorage. Use accessible labels and responsive layout. Include a short
> README with how to run it. Verify JavaScript syntax and report what you tested.
> Do not install packages or use network. Keep changes inside project.

Current Apex exec transport does not expose live context utilization or true
mid-turn steering. Codex app-server documents `thread/tokenUsage/updated`,
`turn/steer` and `turn/interrupt`; adopting that persistent turn transport needs
a separate implementation and tests. This change does not estimate context
usage from cumulative tokens, infer quota from session tokens, or claim steering.
