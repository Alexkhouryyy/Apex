# Apex Code: code on your Claude and ChatGPT plans

Open **Code** from the dashboard sidebar, or go to `/code`.

You tell Apex what to build, fix or explain, by typing or by talking. Your
Claude plan (Claude Code) or your ChatGPT plan (Codex) does the work, and you
watch every step as it happens. It never uses API credits: only the two plans
signed in on your PC (see "Set it up" below).

## Why it's safe to let it loose

Every session works on **its own branch, in its own copy** of the project (a
git worktree under `ApexWork\code\`).
- Your project stays untouched while Apex works. That includes Apex itself,
  which keeps running normally.
- **Keep** merges the session into your branch. **Throw away** deletes the
  copy and the branch, and nothing else changes.
- After every message, Apex saves a checkpoint on the session's branch.
  **Undo last step** puts the files back to before the last step, and Apex is
  told, so it doesn't assume its work is still there.
- If your project has unsaved changes when a session starts, the session says
  so: it starts from your last commit.

## The page

### Build an idea or improve a project

Choose **Add a project**, then either select an existing Git project or check
**Create a new project for my idea**. For a new project, enter the full path
of a folder that does not exist yet, inside an existing parent folder. Apex
prepares Git and an initial README checkpoint. It rejects existing folders
and folders inside another Git project. Describe the idea in the composer
and start the session. Generated changes stay in the session's worktree until Keep.

### Keep talking while it builds

**Send** becomes **Queue next** while a step runs. It saves your follow-up
with the chosen plan, model, effort and mode. The visible **Next steps** list
runs in order after successful, saved coding steps; Pause and Remove control
what is waiting. **Stop** is a separate button and also pauses waiting steps.
At most ten follow-ups can wait in one session. Retrying the same queued
submission after a lost response does not run it twice.

The queue pauses after a failed or stopped step, a plan that needs your
approval, a blocked command, an unsaved checkpoint, or an Apex restart.
It also pauses when you add a follow-up during checks, review or a terminal
operation. Review what happened, then explicitly Resume once the session is
idle. Queue entries survive a restart; they never resume automatically.
While the queue is paused, explicit actions such as **Build it** or **Allow
once** can complete the current task; waiting follow-ups stay paused.

The **Build → Inspect → Verify → Keep** strip links to the existing changes,
checks and Keep controls. Verification comes from the existing proof system;
Keep still asks before accepting work without current proof.

Composer drafts stay with each project or session on this browser. They are
stored locally for up to 30 days, with at most 20 contexts retained. Normal
message failures preserve the draft. Switching sessions or typing a new
thought while an earlier submission completes does not clear the new text.
These browser drafts are separate from Celine's suggested messages.

- **Home:**
  - a greeting, with your two plans as reactor rings: ready, resting (at a
    usage limit, with the time it resets) or not signed in;
  - this week's numbers: sessions, kept, average rating, minutes of AI work,
    and $0 of API credits;
  - the brief: what you want, which plan, and Safe or Full. Ctrl+Enter sends.
    The microphone lets you talk instead: Apex transcribes it on your PC, with
    no API credits;
  - quick starts (Build from an idea, Fix a bug, Explain, Write tests,
    Improve a feature, Brutal review), then your recent sessions with their ratings.
- **A session:**
  - your message, then Apex's live timeline:
    - what it's thinking;
    - its plan as a checklist that ticks itself off;
    - the files it reads (grouped);
    - every edit with its real +/− line counts (click one to see the diff);
    - every command with ✓ or ✗ and its output;
    - anything Safe mode blocked;
    - a checkpoint after each step;
    - a running clock and its last step while it works;
  - when it's done: how long it took, the files changed, its summary, and
    one-click Second opinion, Run checks and Read it to me (in Apex's voice);
  - the side panel:
    - the changed files, each opening a diff with line numbers (↑/↓ for the
      next file);
    - the second opinion's rating;
    - your checks;
    - Keep it → your branch, Undo last step, Catch up, Open folder (copies the
      path, with a link to open it in VS Code) and Throw away.
- **Phone:** the same page, one column. The session list and the change panel
  slide in. When a step takes over a minute, you get a notification when it's
  ready, with what the proof says ("checks passed: 212 passed" or "not
  verified yet"). Tapping it opens the session. A finish at 1 a.m. can be held
  until you're around (Apex's restraint).
- **Away mode.** When Safe mode stops a command, Apex also asks on your phone:
  "Fix login": your Claude plan wants to run `npm install sharp`. Allow it
  once? The link opens one question, Allow once or Don't allow, for two hours.
  - A key or token in the command is redacted in the notification (which also
    reaches every device and Telegram) and on the phone's page; Allow once
    still runs the command exactly as it was stopped. A night task's summary
    is redacted the same way before it reaches Work.
  - A phone paired with its own device token can answer it, even though it
    can't open the rest of Apex Code. The answer is recorded with the device's
    address and browser.
  - Always allow is only on the Code page at your PC, so a tap on a phone can
    never widen Safe mode for good.
  - Don't allow tells the plan, ahead of your next message, to find another
    way or stop and explain.
  - Once you allow it at the PC, the phone's link is answered too, so it can't
    run twice. A session that moved to Codex can't be allowed one command from
    the phone (Codex would get full access): answer that one at the PC.
- **Ask Celine.** 🎙 Ask Celine (or Ctrl+Shift+Space, or `/celine <question>`)
  asks about the open session: "what did Claude just change, and is it safe
  to keep?" Her answer shows in her card and is said in the voice you chose
  for the companion. ✕, Quiet, opening another session or going home stops
  her answer: it isn't finished, shown or said.
  - She reads what Apex saw: the proof verdict and Apex's own checks, the
    second opinion, which files changed (counts, never the diff), the last
    steps and your rules. All of it is treated as untrusted data.
  - She can't keep, throw away, allow a command or send a message. In a
    Work-mode companion turn she can run the checks, ask for a second opinion,
    stop, or draft a message; a draft shows in the feed with Send, Edit and ✕,
    and only you send it.
  - When you tell her how you want your code done, she suggests it as a
    memory: it waits in **Waiting for your OK** like a session's suggestion.
    Any turn that read a session (`code_status`, `code_act`) does the same
    with `remember`, so the agent's words can't become a memory on their own.
  - Apex Code is the owner's: a device token can't ask her about a session.
    Only the owner's own turns (a master-token chat or companion turn, local
    voice and the TUI) are offered `code_status`; chat from a device token,
    SMS, Telegram and other channels are not. A conversation that read a
    session (a Code-page question, or any turn that used `code_status` or
    `code_act`) becomes the owner's: a device token can't list, read, continue
    it or fetch its durable task.
- **Narration.** The selector in the session head says milestones out loud
  (Done, the checks, the second opinion's score, a draft from Celine) in your
  Voicebox voice; Play-by-play adds "running pytest" or "editing upload.py", at
  most one phrase every 6 seconds. It never reads code, or anything shaped
  like a key or token (that goes for Celine and Read it to me too). Without Voicebox it
  chimes instead: it never uses OpenAI's paid voice.
- **Night shift.** Link a software project in Work (Projects, "Code project")
  to an Apex Code project, and mark a task `+apex`. With Always on and Work on
  its own switched on, Apex takes one such task at a time, from your evening
  check until "Night shift until" (06:30 by default), as a real session here
  (🌙 night), in Safe mode, on a plan (never API credits). Then it runs your
  checks and asks the other plan for a second opinion (skipped, and said so,
  if that plan is resting). It never keeps anything. A task that can't start
  there (say, every slot is busy) is marked failed with why, and the night
  moves on to the next one; it is not tried again on its own.
  - Only the owner (master token) links a project, and only the owner can add
    or edit a `+apex` task in a linked project, since its title and notes
    become the prompt. A Work project that just shares an Apex Code project's
    name is not linked.
  - At the evening check: "Tonight I'll take: Fix login, Retry uploads." Untick
    "Apex can take this" on a task to skip it.
  - The morning brief adds "Built overnight", from what Apex saw: "Fix login:
    ready to Keep, checks passed (212 passed), 8/10 by ChatGPT", "Retry
    uploads: not verified (checks couldn't run)". Its link opens "While you
    slept" here, with Keep, Throw away and Open on each. With narration on, the
    page says only the counts out loud, never the titles.
  - A command Safe mode stops at night asks your phone at normal priority, so
    it can wait until you're up; that link lasts 12 hours.
- **Keys:** Ctrl+Enter sends. Esc stops Apex mid-step. `/` jumps to the brief.

## The power tools

It works like Claude Code or Codex, in a page.

- **Live typing.** The AI's words appear as it writes them (Claude Code's
  `--include-partial-messages`). A command's output streams while it runs.
  Everything arrives over one open connection the moment it happens, and
  polling takes over if that connection drops.
- **Inline diffs.** Every edit shows its red and green lines in the feed,
  with real line numbers, found in the file as it is after the edit.
  Click the file name for the whole diff.
- **Allow once / Always allow.** When Safe mode stops a command, you can run
  that exact command now, or let this project's sessions run it with any
  arguments from now on. That's like Claude Code's permission prompt. On
  Codex, which can't allow one command, the message gets full access.
- **Model, effort, Plan first.**
  - Pick Fable, Opus, Sonnet or Haiku for Claude, or type a model name for
    ChatGPT.
  - Effort goes from low to max.
  - **Plan first:** the AI reads, then writes a plan and changes nothing
    until you press **Build it**.
- **`@` files.** Type `@` and pick any file in the project. The AI is told to
  read those first.
- **`/` commands.** `/plan`, `/review`, `/test`, `/undo`, `/keep`, `/push`,
  `/catchup`, `/discard`, `/model`, `/effort`, `/claude`, `/chatgpt`,
  `/safe`, `/full`, `/files`, `/history`, `/terminal`, `/new`, `/look`,
  `/sound`.
- **Terminal.** `!command` in the chat box, or the Terminal tab (↑/↓ for
  history; Ctrl+\` opens it). It runs in the session's copy with your
  project's `.venv`, and the output streams into the feed. It waits while
  Apex works or the checks run, and Keep, Undo, Catch up and the checks wait
  for your command, so nothing changes the files under a check.
- **Files.** The Files tab is the project's file tree with a fuzzy filter.
  The viewer shows the code with colours and line numbers, and **@ Mention**
  points the AI at that file.
- **History.** Every checkpoint of the session, each with its diff.
- **Ctrl+K.** Everything you can do, every session and every file, by name.
- **Keep & push.** Keep it, then `git push`. If the push fails, the work is
  still kept and the feed says why.
- **Tokens.** Each step and the whole session show how many tokens they used,
  from your plan, never API credits.
- **Terminal look** (the default): dense and monospace like Claude Code.
  **Studio look** is the roomier one. Switch with the button in the session's
  header or `/look`.
- **A chime** when a step ends, so you can look away (`/sound` turns it off).

## Safe and Full

| | Claude plan (Claude Code) | ChatGPT plan (Codex) |
|---|---|---|
| **Safe** (default) | Reads, writes and edits files, searches the web. Runs only `git status/diff/log/show`, `ls`, tests (`python -m pytest`, `pytest`, `npm test`, `npm run test/lint`) and syntax checks (`python -m py_compile`, `node --check`, `node scripts/…`). Anything else is blocked, and the feed says what. Only Apex's own memory server: read-only tools, plus remember, which waits for your approval. No other MCP servers or claude.ai connectors. | Its workspace-write sandbox: writes only inside the session's copy, no network. |
| **Full** | Any command, in the session's copy. | Full access. |
| **Second opinion** | Read-only: it can't change a thing. | Read-only sandbox. |

In both modes the agent is told not to commit, push or switch branches,
because Apex records its work. Your project's own `.venv` comes first on its
PATH, so `python` runs your tests with your dependencies. On Windows, the
message goes to the tool on stdin, never on the command line (the same
protection as Work tasks).

## The second opinion

The other plan reads the change, read-only, and answers in a fixed shape:
`Rating: N/10`, a one-line verdict, problems (most serious first, with
file:line), and what's good. It's told to be brutally honest.
Claude reviews get only Read, Glob and Grep, plus Apex's reading MCP tools;
project command permissions are never inherited. Run **Checks** separately
for test evidence, since test scripts can write files. Codex reviews use its
read-only sandbox. Keep, Undo, Catch up, reviews, checks, messages and terminal
commands reserve the session so Apex cannot run them on top of each other.
**Fix what it found** sends its findings back to the session's plan, which
fixes the real problems and says why it skips any it disagrees with.

## What only Apex adds

Claude Code and Codex on their own forget you between sessions and take the
agent's word for what it did. These features use what Apex already knows and
what it sees for itself. Every one of them is tested against fake `claude`
and `codex` programs; none has yet been run in a real session on your plans.

1. **A brief about you in every session** (`agent/code_brain.py`). A new
   conversation starts with at most 4000 characters, each line tagged with
   its source: `APEX_USER.md`, your standing rules, this project's rules, its
   handoff, up to 8 memories (coding preferences first, then preferences and
   decisions that share words with the request), what was measured on this
   project, and how the last 3 sessions ended. Secrets are redacted.
   - Only memories you vouched for go in: one you approved (at the master
     token), typed yourself, or a rule for all code. Anything chat, a phone,
     Telegram or a tool call saved on its own, and personal facts, stay out;
     the agent can still look those up through Apex's memory server, which
     logs it.
   - Coding memories saved before Apex kept track of who saved what, or from a
     chat or a phone, are listed in the dialog under **Not told until you OK
     it**. **It's mine, use it** (Code page only, master token) adds one to
     the brief; **Forget** deletes it.
   - A past session is named with what Apex measured (kept or thrown away and
     why, rating, checks), never the agent's own summary; the decision log
     doesn't quote it either. Claude gets it as a system-prompt file
   (`--append-system-prompt-file`, kept beside the working copies, deleted on
   Keep or Throw away); Codex gets it ahead of the message. The card under
   your first message shows what was sent, with **Forget** on each memory; the
   home chip "🧠 Apex knows N things" and Ctrl+K open the same list.
2. **Say it once: rules.** A message that corrects a finished step ("no,
   don't…") offers "Make this a rule for {project}" or "Remember for all my
   code" (only that button makes a rule for all code; a memory tagged
   `code,rule` by a model is not one). The **Rules** tab (and `/rule <text>`) turns rules on and off, edits
   them, and restores earlier versions. A change reaches new sessions through
   the brief, open sessions on their next message, and the second opinion,
   which is asked to flag any rule the change breaks.
3. **The proof card.** Checks end as passed, failed or **unknown** (could not
   run, stopped, timed out, or a test runner that printed no count); "Exit 0
   counts as a pass" is a project setting. The card puts what the agent said
   ("ran the tests, all pass") beside what Apex saw: its own checks, the
   agent's test runs, your terminal runs, and output you paste (marked as
   yours; it can never make the verdict Proved). It flags a pass on files that
   changed since, edited test files, a second opinion from the same plan that
   did the work, and file:line citations that don't exist. **Keep** asks again
   when the verdict isn't Proved.
4. **Every session teaches Apex.** Keep and Throw away (which now asks why)
   add a line to the project's decision log, today's vault note and an
   outcome row. From those sessions Apex counts what happens on each project
   ("thrown away 5 of 5 when no checks ran", "Claude plan: kept 7 of 9, 6
   with proof"), only once there are 5 sessions, always as counts. The counts
   show on the home page and in the composer hint, and reach the next brief.
5. **Apex's memory inside the session.** Each session runs Apex's own MCP
   server (`scripts/apex_mcp.py`, turned off with `CODE_APEX_MCP=false`), so
   the agent can look up memories and this project's rules mid-task. A memory
   it suggests waits in **Waiting for your OK** (Approve, Edit, Reject); the
   second opinion can't suggest any.
6. **Away mode, Ask Celine, Narration and the Night shift**: see the page
   section above.

Not done or not seen yet:
- No real session on your plans has run any of these. Codex's
  `-c mcp_servers.apex.*` options were only checked as valid TOML, not
  against a real `codex exec`; Claude Code 2.1.294 accepted
  `--append-system-prompt-file` and `--mcp-config` without running a prompt.
- An open Claude conversation hears a new rule only on your next message; its
  system prompt is the brief it started with.
- Rules are edited only inside a session (the Rules tab), not from home.
- The project handoff's "next step" is not written by Apex; only the decision
  log is.
- Telegram, the fallback for phone notifications, gets no link, so a blocked
  command can't be answered from it.
- From the Code page Celine answers in Discuss mode; she can run checks or
  draft only in a Work-mode companion turn. No wake word, no barge-in, and
  nothing was tried against a real Voicebox or Whisper here.
- The night shift takes one task at a time, and keeping the PC awake and
  holding a 3 a.m. ask until morning were only exercised in tests, not on a
  real Windows PC overnight. "While you slept" shows only from the morning
  link (`/code#overnight`).
- At most 50 pending approvals of any kind are listed, so a crowded queue can
  hide a suggested memory from the box.

## When things go sideways

- **A plan hits its usage limit:**
  - the step ends with "Plan limit reached" and the reset time;
  - that plan rests (the same rest the always-on Work agent uses);
  - **Continue on your ChatGPT plan** (or Claude) carries on with a recap,
    because one tool can't resume the other's conversation;
  - the page picks the ready plan for you when you open a session whose plan
    is resting.
- **The tool lost the conversation** (its session files were cleared): Apex
  starts it fresh with a recap of what was asked, the last summary and what
  changed. You don't have to do anything.
- **Your branch moved on:**
  - **Catch up** brings your latest commits into the session;
  - if they clash, the session says where, and **Ask Apex to fix them**
    resolves the conflicts;
  - Keep stays locked until the conflict markers are gone;
  - if Keep would clash with your newer work, it refuses and says to catch up
    first. If it would overwrite your unsaved changes, it refuses and names
    the files.
- **Apex restarts mid-step:** the session shows "Interrupted". What it changed
  is still in the session, and your next message carries on.
- **Stop** (or Esc) ends the tool and everything it started, even in the
  first second.

## Set it up (once)

1. Run `Setup-Apex-Work-Plans.cmd` if you haven't (see `docs/WORK.md`). Apex
   Code uses the same two sign-ins.
2. You need Git for Windows (you already have it if `git pull` works).
3. Check Apex Code's own path on both plans: a live coding step and a
   follow-up, a few seconds of each plan's usage:
   ```
   python scripts\work_plans_check.py --code
   ```
   Both should end with READY.
4. Your name on the page comes from `OWNER_NAME` (default: Alex).
   Projects: Apex itself is always there; **Add a project folder** adds any
   other git project on your PC.

## Honest limits

- **Checked against the real apps:**
  - Claude Code 2.1.291 accepted every option Code passes, in all three
    modes, `--resume` included. Code turned its real error output into a
    clear failure;
  - Codex 0.160.1 accepted every option for new and resumed sessions (the
    sandbox is set through `-c sandbox_mode=`, which `exec resume`
    requires). Code read its real stream, including its thread ID;
  - nothing used any plan for these checks.
- **Not yet seen:** a full session on your own plans. Running
  `work_plans_check.py --code` on your PC covers that.
- **Usage:** your plans' usage limits apply, and a long session uses a lot of
  them.
- **Full mode** really can run anything in the session's copy. Use it for work
  you trust.
- **Restart after Keep:** if you keep a change to Apex itself, restart Apex to
  run it.

## Checks

- `tests/test_code_studio.py`: real git repositories, with fake `claude` and
  `codex` programs that stream the same JSON the real ones do and really edit
  files. It covers:
  - a session in its own copy, with your project untouched;
  - every kind of step in the feed;
  - follow-ups resuming the same conversation;
  - one message at a time, and at most 3 sessions working at once;
  - Stop ending the tool and its helpers;
  - Keep merging and tidying up;
  - Throw away;
  - Undo;
  - switching plans with a recap;
  - a lost conversation restarted;
  - a plan limit resting the plan;
  - the second opinion and its rating;
  - checks;
  - catching up, and conflicts fixed by Apex then kept;
  - Keep refusing to overwrite your unsaved changes;
  - projects and validation;
  - restart recovery;
  - the command line per mode;
  - the real tools' error lines;
  - the plan checker's `--code` path;
  - the owner-only, same-site API.
- `scripts/check_code_ui.cjs`: the page against a stubbed API. It covers:
  - the greeting and plans, and starting from the brief;
  - every step of the feed. Agent text never becomes HTML;
  - Esc stops a running step;
  - the second opinion ring, the side panel, and the diff with line numbers;
  - Keep only on a real "yes": Esc or Cancel never keeps;
  - a resting plan is never offered by default;
  - the brief card, rules, proof card, track record, memory box, away mode,
    Celine and the night shift.
- `scripts/check_code_narration.cjs`: spoken phrases never carry code or
  titles, and play-by-play is paced.
- `tests/test_code_studio.py` also covers the brief, rules, proof, write-back
  and track record, the memory server, away mode, Celine's tools and the
  night shift; `tests/test_mcp_server.py`, `tests/test_companion.py` and
  `tests/test_continuity.py` cover their halves.
- Real Chromium: the whole page against the real backend, with demo tools, on
  desktop and phone, with no horizontal overflow and no console errors.
