# Work: one place for your job, studies, business and software

Open **Work** from the dashboard sidebar, or go to `/work`.

## What it does

- **One line adds a task**, with shorthand:

  | Type | Means |
  | --- | --- |
  | `#job` `#studies` `#business` `#software` | the area |
  | `@project` | file it under a project (the start of its name is enough) |
  | `!high` / `!low` | priority |
  | `today`, `tomorrow`, `fri`, `next week`, `12/10` (day/month), `2026-10-20` | the due date |
  | `waiting` | starts it as waiting on someone |

  For example: `send invoice to Karim fri @website !high`. All-caps words are
  kept as words, so `prepare for the SAT` isn't read as Saturday.

- **Today:** what's overdue, due today, Apex's finished work waiting for your
  review, what Apex is working on, the next 7 days and what's waiting on
  others. At the top: the counts, and one sentence on the day.
- **Board:** To do, Doing, Waiting, Review, Done. Drag a card to change its
  status.
- **Projects:** group tasks by client or course, see what's open and late in
  each, and archive finished projects.
- **Area filters** across the top.
- **Click any task** to edit its date, priority, area, project, notes and who
  it's waiting on.

## Hand a task to Apex

Open a task, choose who does it (**Claude plan**, **ChatGPT plan** or **API
credits**) and press **Give to Apex**.
1. On a plan, Apex runs Claude Code or Codex, signed in with your own account,
   inside the task's folder (see "Always on, on your plans" below). On API
   credits, it runs its task runner with a spending cap (default $0.50):
   research, then write, then an independent review pass.
2. It writes the deliverable into its own folder, `%USERPROFILE%\ApexWork\<task>\`.
3. The task comes back as **Apex finished, your review**, with Apex's
   summary, the files it made and what it cost.
4. **You** decide whether it's done; Apex never marks it done itself.

Apex prepares drafts but never sends, pays or contacts anyone. Only the
owner's master token can hand work to Apex, because it lets Apex write files.
If Apex is stopped or blocked, the task comes back to **To do** with what it
got to.

## Always on, on your plans

The **Always on** tab turns Apex into an agent that keeps working while you
don't look. It is off until you switch it on.

- **Always on:**
  - a morning brief of Today on your phone and PC (default 08:30);
  - an evening check if something from today is still open (18:00);
  - a nudge when a task has been waiting on someone for 3 days, then every 3
    days;
  - a note the moment Apex finishes or stops a task.
- **Work on its own:** Apex picks up the tasks you marked for it (`+apex` in
  quick add, or "Apex can take this" in the task).
  - The most urgent goes first, one at a time.
  - Finished work comes back to **Review**. It is never marked done for you.
  - A task Apex tried and couldn't finish is never retried on its own.
- **Who does the work**, in your order. By default your **Claude plan**, then
  your **ChatGPT plan**:
  - they run through the official apps, signed in with your own account, so
    tasks count against your plans' usage, not API credits;
  - Apex removes its API keys from what it passes to those apps, so they can
    only use your sign-in;
  - when one plan hits its usage limit, the task goes back untried and the next
    plan takes it; the limited plan rests for 5 hours (you can change this, or
    press **Try resting plans now**);
  - **API credits** are only used if you tick them, and then only within the
    per-task and per-day caps.
- **Plan tasks a day** (default 8) stops Apex from using up your week's usage
  on its own.
- **What Apex did** lists everything it did, newest first.

What the plans may do:
- Claude Code may read, write and edit files in the task's folder and search
  the web. It has no shell.
- Codex runs in its workspace-write sandbox, in the task's folder.
- Each run stops after 30 minutes.

### Set it up on your PC (once)

1. Install Node.js LTS from nodejs.org if you don't have it.
2. Double-click **`Setup-Apex-Work-Plans.cmd`** in your Apex folder. It installs
   Claude Code and Codex, then opens each so you can sign in:
   - Claude Code: choose your **Claude account** (Pro or Max), not an API key;
   - Codex: choose **Sign in with ChatGPT**.
3. Restart Apex and open **Work → Always on**. Both plans should say
   **ready**.
4. Tick **Always on** and **Work on its own**, then **Save**.
5. Add a test task: `summarise the pros and cons of a standing desk +apex`.
   Within a minute it should show **Apex is working**.

### Honest limits

- Your plans' own usage limits apply. Heavy days will hit them, and that's
  what the fallback is for.
- This covers Work tasks only. Apex's normal chat and voice still use its
  configured model and API key.
- These apps sometimes change their sign-in or output. If a plan says "not
  signed in", run `claude` or `codex` once in a terminal.
- Not yet run against real subscriptions: the checks use fake `claude` and
  `codex` programs. The first real run on your PC is the real test.

## From chat and voice

The `work` tool works anywhere you talk to Apex:
- **"Add a task: call the accountant next week, waiting"**
- **"What's on my plate today?"** gives overdue, due, waiting, and Apex's
  finished and in-progress work.
- **"Mark task 12 done"**
- **"What is the work agent doing?"** says if it's on, which plan it's using,
  and whether a plan is resting.

## Where it's stored

In Apex's own memory database, with your other data: on this PC, and backed
up with it.

## Checks

- `tests/test_work.py`:
  - every shorthand, including a weekday on that same weekday, day/month
    dates that roll into next year, and the SAT case;
  - validation;
  - where every kind of task lands on Today;
  - handing a task to Apex: the brief includes notes and project; it comes
    back for review with summary, cost and files; a block returns it to you;
    a busy runner is a clear error;
  - the chat tool;
  - the API, including the cross-site and owner-only rules.
- `tests/test_work_agent.py` (fake `claude` and `codex` programs on PATH):
  - each plan runs in the task folder;
  - no API key reaches it;
  - the task text goes on stdin, `&` and `%` included;
  - finished, limit reached, signed out, failed, missing and timed out are
    told apart, and a finished answer that mentions limits stays finished;
  - a limit puts the task back untried and rests that plan;
  - a restart mid-run shows as interrupted;
  - the loop: brief and evening once a day, waiting nudges, one task at a
    time, falling over to the ChatGPT plan, holding when no plan is free,
    never using credits unless chosen, the daily plan-task limit, and no
    retries;
  - settings validation, the chat answer, and the owner-only API.
- `scripts/check_work_ui.cjs`: the page's views, area filter, quick add,
  drag-to-column, the detail panel, Give to Apex on the chosen plan,
  "Apex can take this", the Always on panel, and ticking done.

## Next, if you want them

- **Your calendar and email** on Today, once Gmail and Calendar are connected
  in Apps.
- **"Plan this":** Apex breaks a big task into dated steps, and you approve
  them before they're added.
- **Recurring tasks**, for example "invoice every 1st of the month".
