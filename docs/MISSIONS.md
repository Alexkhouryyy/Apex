# Missions: keep working until it's actually done

A **team task** runs research, then coding, then review, once, and stops
whether or not the job is finished. A **mission** wraps that in a loop. It
keeps going round after round, and after every round it runs checks you chose
up front. It ends when the checks pass, or when a limit says stop.

Open **`/missions`** (also linked from the dashboard's task workspace).

## How a mission works

1. **Start it.** You give a title, what to do, and a **finish line**: 1–10
   checks Apex can run itself.
   - **Command passes:** a command exits 0, for example `python -m pytest -q`.
     It runs in Apex's Docker sandbox, so **Docker Desktop must be running**.
     Without Docker, Apex refuses to start a mission with a command check,
     because that check could never pass.
   - **File exists:** the file is there and isn't empty.
   - **File contains:** the file includes the text you give.

   A check only you can confirm ("looks good to me") isn't allowed, because
   it would never pass on its own. Confirm it yourself at the end.
2. **Each round** is a team task: research, code, review. Apex is told the
   mission, the checks, and, from round 2 on, exactly which checks failed and
   what they reported. It's also told to inspect the current state before
   redoing anything, and not to repeat an approach that already failed.
3. **After each round** the checks run (`agent/verification.py`). That module
   fails closed: a check that can't run counts as failed, never as passed.

## When it stops

| Status | Why |
| --- | --- |
| **Done ✓** | Every check passed. |
| **Stuck** | All rounds used, or **the same checks failed the same way 3 rounds in a row**. More rounds would only spend money. |
| **Out of budget** | It reached its spending cap: $5 by default, at most $20. Each round can spend at most $1.50. |
| **Needs you** | A step needs a human. See the list below. |
| **Paused / Stopped** | You pressed it. Work already done stays done. |

Reasons a mission asks for you:
- A tool was blocked by Apex's safety gates. The team is told never to send,
  publish, deploy or contact anyone.
- A round was cut off in the middle of an action, so its result is unknown.
- Apex's daily spending cap was reached.
- A round crashed, for example a bad API key or no network. Another round
  wouldn't fix that, so it stops instead of burning rounds.

A mission that is stuck or out of budget can be resumed with more rounds or
more money. You get a phone notification when a mission finishes or needs you.

## It doesn't sleep (within honest limits)

- **While a mission runs, Windows is asked not to sleep.** Closing a laptop's
  lid can still override that, depending on your power settings.
- **When Apex restarts, a running mission carries on by itself.** The
  exception: if its interrupted round left an action whose result is unknown
  (for example a command cut off halfway), it waits for you. Repeating an
  action whose result is unknown is how work gets done twice. Check the round
  under *Team tasks*, then press Resume.
- It runs on the Apex computer. Closing the browser page doesn't stop it.
  Turning the PC off does.

## Who can start one

Starting and resuming a mission needs the **master** dashboard token, because
a mission works unattended with the coder's file and shell tools. Anyone
signed in can watch, pause or stop one.

## Limits

- One team task runs at a time, so missions take turns. A mission waits while
  another team task is running.
- A mission only proves what its checks check. "Tests pass" doesn't mean the
  feature is right if the tests don't test it. Choose checks that would only
  pass if the job is really done.
- The models and their costs are the team task's: by default Apex's main model.
