---
name: ship-apex
description: Finish and ship a change in the Apex repo the way its owner expects — targeted tests, revert-audits proving each new guard is tested, the full pytest suite, every page check (with failures compared against main), then commit, PR and merge to main, and a plain-English report with the laptop update commands. Use when a change to Apex is done or nearly done, or when the owner says "merge", "merge everything", "ship it", "next", "keep going", or asks whether something is ready.
---

# Ship an Apex change

Apex is the owner's self-hosted personal AI agent (Python backend in `agent/`,
`dashboard/`, `app/`; static pages in `dashboard/static/`; tests in `tests/`;
page checks in `scripts/check_*.cjs|mjs`). The owner runs it on a Windows laptop
at `C:\Users\alexk\Apex`. They want honest results, not optimism: say what was
verified, what failed, and what has not been seen on the real PC.

Run these steps in order. Do not skip a step because the change "looks small".

## 0. Tools present?

A cloud container starts fresh. `.claude/hooks/session-start.sh` installs
pytest (requirements.txt), jsdom and Playwright on session start. If anything is
missing (`No module named pytest`, `Cannot find module 'jsdom'`):

```bash
CLAUDE_CODE_REMOTE=true .claude/hooks/session-start.sh
```

Never run `playwright install`; the browser is preinstalled in `/opt/pw-browsers`
and the hook pins the matching Playwright (1.56.1 for chromium-1194).

## 1. Branch

Work on the branch the session names (e.g. `claude/...`). If that branch's
previous PR is already merged, start the new work from main rather than
stacking on merged history:

```bash
git fetch origin main && git checkout -B <branch> origin/main
```

If a permission check blocks this, say so and ask the owner; don't work around it.

## 2. Targeted tests, then revert-audits

Run the tests for the files you changed first (`python -m pytest -q tests/test_x.py`).

Then prove every new guard or behavior is actually tested: break it and confirm
a test fails. Use the helper (it always restores the file):

```bash
python .claude/skills/ship-apex/audit.py <file> "<exact text>" "<broken text>" tests/test_x.py
```

`CAUGHT` is good. `GAP` means no test notices that line breaking: add a test,
then audit again. Audit the guards that matter (security checks, defaults,
limits, ordering, fallbacks), typically 4–8 per change, and mention the count
in the report.

## 3. Full suite (background, ~10–12 min)

```bash
python -m pytest -q -p no:cacheprovider
```

Run it with `run_in_background` and keep working. If a test fails, rerun just
that test 3 times on its own. Passing alone on every run, in code this change
didn't touch, means a timing flake under load. Say so explicitly. (Known one:
`tests/test_code_studio.py::test_one_message_at_a_time_and_a_limit_on_parallel_sessions`.)
A failure in code you touched is a real failure: fix it.

## 4. Page checks

```bash
bash .claude/skills/ship-apex/ui_checks.sh            # all ~75
bash .claude/skills/ship-apex/ui_checks.sh companion  # just matching names
```

It retries each failure once, then runs still-failing checks against
`origin/main`. Only **NEW** failures (pass on main, fail here) block shipping.
The `*_browser` checks can time out under load. If a NEW one is for a page your
diff doesn't touch (`git diff origin/main --stat -- dashboard`), rerun it alone
3 times (`ui_checks.sh <name>`). Mostly passing means a flake: report it as
one, with the count.
Report "also fails on main" ones as environment issues, by name. If you changed
a page's JS, also `node --check dashboard/static/<file>.js`.

## 5. Commit and push

Write a clear message: what changed for the owner, then why. End it with the
attribution lines the session's system reminder gives. Never put a model name
in commits, PRs or code.

```bash
git push -u origin <branch>      # retry on network errors: 2s, 4s, 8s, 16s
```

## 6. PR and merge

The owner's standing instruction is to merge finished work to main. Use the
GitHub MCP tools (owner `alexkhouryyy`, repo `ni`; it is Alexkhouryyy/Apex):

- `mcp__github__create_pull_request` with `head` = branch, `base` = `main`.
  Body sections: **What** (bullets, plain English), **Tests** (targeted, audits,
  full-suite count, page-check result with environment failures named),
  **Not yet seen on the PC** (anything only tested with fakes: Windows APIs,
  GPU, real hardware). End with the PR attribution from the system reminder.
- `mcp__github__merge_pull_request` with `merge_method: merge`.

## 7. Report to the owner

Short and concrete:

1. What they get now, as numbered points in everyday words.
2. Verification: suite count, audits, page checks; name every failure and why
   it isn't from this change.
3. **Honest gaps**: what wasn't tried on the laptop, what depends on the
   AI's judgment, and what might be off.
4. The PowerShell to get it (see the `apex-laptop` skill), usually:

   ```powershell
   cd C:\Users\alexk\Apex
   git pull
   ```

   plus which part to restart and one quick way to try the feature.
5. One suggested next step.
