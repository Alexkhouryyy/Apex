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
  - before a plan runs, Apex asks the app how it is signed in (`claude auth
    status`, `codex login status`; this uses none of your plan). If it is
    signed in with an API key, a Console account or a cloud account, which
    would bill credits, Apex refuses it and tells you how to switch;
  - Apex also removes its own API keys from what it passes to those apps;
  - when one plan hits its usage limit, the task goes back untried and the next
    plan takes it. The limited plan rests until the reset time the app gives
    ("try again in 2 hours", "resets 3pm"), or for 5 hours if it gives none.
    You can change the 5 hours, or press **Try resting plans now**;
  - **API credits** are only used if you tick them, and then only within the
    per-task and per-day caps.
- **Plan tasks a day** (default 8) stops Apex from using up your week's usage
  on its own.
- **What Apex did** lists everything it did, newest first.

What the plans may do:
- Claude Code may read, write and edit files in the task's folder and search
  the web. Only those tools exist in its run (`--tools`). It has no shell, no
  MCP servers, and none of your claude.ai connectors (Drive, Stripe...). With
  `--restricted` (Claude Code 2.1.29x and later), its file tools stay inside
  the task folder and your personal Claude settings and hooks are ignored.
- Codex runs in its workspace-write sandbox, in the task's folder.
- Each run stops after 30 minutes. **Stop** in the task ends it sooner.
  Either way, the app and everything it started is ended, so nothing keeps
  running on your plan.
- The task text goes to the app on stdin, never on the command line, so a
  title with `&` or `%` can't be read as a Windows command.
- **Check sign-in** on the Always on tab asks both apps again.

### Set it up on your PC (once)

1. Install Node.js LTS from nodejs.org if you don't have it (Codex needs it).
2. Double-click **`Setup-Apex-Work-Plans.cmd`** in your Apex folder. It:
   - installs Claude Code (Anthropic's installer) and Codex (npm);
   - signs each in through your browser, skipping any that is already signed
     in with the plan. Claude uses `claude auth login --claudeai`, which is
     always your subscription and never Console/API billing. For Codex,
     choose **Sign in with ChatGPT**;
   - runs `scripts\work_plans_check.py --live`. For each plan, this checks the
     install, that it's signed in with the plan, and that it accepts every
     option Apex uses. Then it runs one tiny real task that must write a file.
     It ends with **READY** or **NOT READY** and what to do.
3. Restart Apex and open **Work → Always on**. Both plans should say
   **ready**.
4. Tick **Always on** and **Work on its own**, then **Save**.
5. Add a test task: `summarise the pros and cons of a standing desk +apex`.
   Within a minute it should show **Apex is working**.

### On Windows: two things seen on a real PC

- **Claude says "not signed in" although `claude` opens fine.** Your PC has
  `ANTHROPIC_API_KEY` set, so plain `claude` was running on API credits. Apex
  hides that key from Claude Code, so it needs your account:
  `claude auth login --claudeai`. The setup script does this for you.
- **Codex writes the file, but Windows blocks reading it.** This can happen
  with Codex's Windows sandbox when it runs commands as separate sandbox
  users. The live check retries, shows who owns the file (`icacls`), and tries
  Codex's `unelevated` mode. If that works, it prints the one command that
  sets `WORK_CODEX_WINDOWS_SANDBOX=unelevated`. A real task with this problem
  says so in its summary.

### Honest limits

- Your plans' own usage limits apply. Heavy days will hit them, and that's
  what the fallback is for.
- This covers Work tasks only. Apex's normal chat and voice still use its
  configured model and API key.
- These apps sometimes change their sign-in or output. If a plan says "not
  signed in", run `claude` or `codex` once in a terminal.
- **Seen working on your PC (6 Oct 2026):** the setup signed in Claude
  (Claude Pro) and Codex (ChatGPT). Both passed the live check: a real task
  on each plan wrote its file, and Apex could read it (Claude in 20 s, Codex
  in 50 s).
- Not yet seen: a real usage-limit message from either plan, and a week of
  Always on.

## Coding

For coding, there's a page of its own on the same two plans: **Code**
(`/code`, see `docs/CODE.md`).

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
  - only a plan sign-in counts, using each app's real answers (an API key,
    an API key helper, Bedrock or Vertex, an API key in the environment, or
    signed out are all refused, and a key is never repeated back); the
    answer is cached for ten minutes and **Check sign-in** refreshes it;
  - Stop and the 30-minute limit end the app and the helper it started;
  - reset times in the apps' formats; noise kept out of summaries; a `429`
    inside a timestamp is not a limit;
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
- `tests/test_work_plans_real.py`: against the real Codex and Claude Code
  when installed (skipped in CI). It checks every option Apex uses, and that a
  signed-out or API-key Codex is refused. It uses no plan.
- `scripts/check_work_ui.cjs`: the page's views, area filter, quick add,
  drag-to-column, the detail panel, Give to Apex on the chosen plan,
  "Apex can take this", the Always on panel, and ticking done.

## Next, if you want them

- **Your calendar and email** on Today, once Gmail and Calendar are connected
  in Apps.
- **"Plan this":** Apex breaks a big task into dated steps, and you approve
  them before they're added.
- **Recurring tasks**, for example "invoice every 1st of the month".
