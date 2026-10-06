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

Open a task and press **Give to Apex**, with a spending cap (default $0.50).
1. Apex runs it through its task runner: research, then write, then an
   independent review pass.
2. It writes the deliverable into its own folder, `%USERPROFILE%\ApexWork\<task>\`.
3. The task comes back as **Apex finished, your review**, with Apex's
   summary, the files it made and what it cost.
4. **You** decide whether it's done; Apex never marks it done itself.

Apex prepares drafts but never sends, pays or contacts anyone. Only the
owner's master token can hand work to Apex, because it lets Apex write files.
If Apex is stopped or blocked, the task comes back to **To do** with what it
got to.

## From chat and voice

The `work` tool works anywhere you talk to Apex:
- **"Add a task: call the accountant next week, waiting"**
- **"What's on my plate today?"** gives overdue, due, waiting, and Apex's
  finished and in-progress work.
- **"Mark task 12 done"**

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
- `scripts/check_work_ui.cjs`: the page's views, area filter, quick add,
  drag-to-column, the detail panel, Give to Apex and ticking done.

## Next, if you want them

- A **morning brief** of Today on your phone (Telegram or push notification).
- **Your calendar and email** on Today, once Gmail and Calendar are connected
  in Apps.
- **"Plan this":** Apex breaks a big task into dated steps, and you approve
  them before they're added.
- **Recurring tasks**, for example "invoice every 1st of the month".
