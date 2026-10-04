# The minimum complete demonstration

The roadmap's acceptance test for Apex as an environment, run by one command:

1. Create a project, reference source material, generate action items,
   produce a document, and save the decision and next step.
2. Restart and resume.
3. Repeat in a second project and check the two stay separate.
4. Read from one app, then make one write you authorized, with a receipt.
5. Interrupt a task mid-action and recover without repeating the action.

Each step reports **PASS**, **FAIL**, **UNKNOWN** or **SKIPPED**, with its
evidence. The report goes to `report.md` and `report.json` in the demo
folder.

## Your memory is safe

The demo always makes its own folder, `~/.apex/demo/<date-time>/`, with its
own database. It refuses to run if the database isn't in that folder.

- Your real memory, projects, board and documents are never read or changed.
- Every phase runs in a new process, so "restart" is a real restart, not a
  re-read inside one program.

## Run it (Windows, in the Apex folder)

**Fixture mode.** This uses a scripted model and a pretend notes app. It's
free, needs no network, and takes about 10 seconds:

```cmd
.venv\Scripts\python scripts\apex_demo.py
```

This proves Apex's own parts work together: projects, handoffs, the app gate
and recovery. It proves nothing about a model or an account.

**Live mode.** This uses your configured model (`AGENT_MODEL`). It costs a
few cents.

```cmd
.venv\Scripts\python scripts\apex_demo.py --live
```

The model writes the action items and the document. In the recovery step, a
real model decides whether to repeat the interrupted write. That's the real
test: a careless model appends the line twice, and the step fails.

### Adding the app steps (live)

Without app tools named, the app steps say **UNKNOWN** and nothing is sent.
To include them, connect an app in **Apps** first. Then name one read tool,
one write tool, and a second read that should show the write:

```cmd
.venv\Scripts\python scripts\apex_demo.py --live ^
  --read-tool app__notion__NOTION_SEARCH_NOTION_PAGE --read-args "{\"query\": \"Apex demo\"}" ^
  --write-tool app__notion__NOTION_CREATE_NOTION_PAGE --write-args "{...}" ^
  --check-tool app__notion__NOTION_SEARCH_NOTION_PAGE --check-args "{\"query\": \"Apex demo\"}" --expect "Apex demo"
```

The names above are an example. Your tool names are in **Apps** after you
connect an app.

**The write goes through Apex's normal write gate.**
- The demo shows you the exact tool and arguments.
- Nothing is sent unless you type `YES`.
- If you refuse, the step says **SKIPPED** and nothing is sent.

**The write's result:**
- **PASS:** there's a receipt (the provider's log ID) *and* the second read
  shows the change.
- **UNKNOWN:** there's a receipt, but you didn't name a check read.

## How the interruption works

1. A task gets one job: append `INVOICE-42 sent` to a ledger file in the demo
   folder.
2. The task's process is killed right after the write lands and before Apex
   records the result. This is the "did it go through?" moment.
3. A new process opens Apex and checks four things:
   - the task shows as **interrupted**, with one action of **unknown
     outcome**;
   - continuing without reviewing that action is refused;
   - after a review (the demo reads the ledger file and records what it saw),
     a continuation runs;
   - the line is in the ledger **exactly once**.

The action in step 1 is always scripted, even in live mode, so the crash
happens at the same point every time. The recovery is Apex's real code, and
in live mode the model is real.

## Prove the checks can fail

A check that can't fail proves nothing. Each `--fault` breaks one thing on
purpose (fixture mode only):

```cmd
.venv\Scripts\python scripts\apex_demo.py --fault leak           # project B's handoff carries A's decision
.venv\Scripts\python scripts\apex_demo.py --fault repeat-write   # the recovery appends the line again
```

Each one should report exactly one **FAIL**, at that step.

## What it found

**Documents are not per project.** Apex keeps one shared list of documents.
Only the project handoff links a document to its project. That's why the
demo reports **UNKNOWN** for this check instead of PASS. Changing it means
adding a project to each document; it's not built yet.

## Send it back

After a live run, send the summary at the end (`pass: … fail: …`) and any
FAIL rows. They go into `docs/CAPABILITY_MATRIX.md` as dated results.
