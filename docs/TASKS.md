# The task suite: does it do the work, and what did it cost?

This answers the roadmap's Phase 2 question with one command: can Apex finish
real tasks end to end, with evidence you can inspect and a recorded cost?

```cmd
.venv\Scripts\python scripts\apex_tasks.py --live
```

## The two tasks

| Task | What Apex must do | Checked by |
| --- | --- | --- |
| **Brief to plan** | Read a Word brief and an Excel budget, then write `plan.md`: 3–6 action items, each citing its source file, plus the budget total | The file is parsed. The section must exist, every bullet must cite its source and share content words with the files, and the total must equal the sum (173) |
| **Sheet to tracker** | From a spreadsheet, pick the items still to buy that cost over $50, and write them to `tracker.csv`, highest cost first | Exact header, exact rows, exact order |

**For both tasks:**
- every input file must have been read (from the runner's own record);
- the inputs must be byte-identical afterwards;
- no other file may appear.

**No model grades another model.** Every check is a rule a test can
reproduce. The roadmap's other example, a task through a connected app,
waits for you to connect one. The app part of `scripts\apex_demo.py` covers
that path's gate and receipt.

## What each run records

Each run gets its own folder, `~/.apex/tasks/<date-time>/`, with its own
database; your real memory is never used. The folder holds:
- `report.md`: each check with its evidence, the tools used, the time, tokens and cost;
- `receipt.json` per task: Apex's own record of every tool call and its status;
- the files the model wrote, so you can open them.

**Cost** is read from Apex's usage ledger, the same place the dashboard's
spending figures come from. It's checked against the task runner's own total;
if the two disagree, the run fails. Each task is capped at $0.25 by the
runner.

## Compare models

```cmd
.venv\Scripts\python scripts\apex_tasks.py --live --model deepseek-flash
.venv\Scripts\python scripts\apex_tasks.py --live --model claude-opus-5
.venv\Scripts\python scripts\apex_tasks.py --history
```

`--history` lists every run: date, model, task, pass or fail, cost and time.
That's the evidence for picking a model by results and price, not by
reputation.

## Prove the checks can fail

```cmd
.venv\Scripts\python scripts\apex_tasks.py --fault ungrounded     # adds an action item that isn't in the files
.venv\Scripts\python scripts\apex_tasks.py --fault wrong-filter   # adds a row that shouldn't be there
```

Each one fails exactly one check. Without `--live`, a scripted model stands
in, which proves the harness, not a model; its cost comes from made-up token
counts.

## Limits

- **The grounding check is word overlap.** It catches invented items (a
  plumber in a workshop plan), not subtle misreadings.
- **Two tasks are a smoke test, not a benchmark.** They show the whole path
  works and what it costs. Add tasks to `TASKS` in `scripts/apex_tasks.py` as
  real workflows come up.
- **The task runner's coder can also run commands.** The tasks tell it to
  stay in its folder, and the checks fail any change in it. A command run
  elsewhere on your PC would not be caught. Run live only on a machine where
  that's acceptable, the same as the Constellation tab.
