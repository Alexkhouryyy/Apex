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

- **Home:**
  - a greeting, with your two plans as reactor rings: ready, resting (at a
    usage limit, with the time it resets) or not signed in;
  - this week's numbers: sessions, kept, average rating, minutes of AI work,
    and $0 of API credits;
  - the brief: what you want, which plan, and Safe or Full. Ctrl+Enter sends.
    The microphone lets you talk instead: Apex transcribes it on your PC, with
    no API credits;
  - quick starts (Fix a bug, Add a feature, Explain, Write tests, Make it look
    better, Brutal review), then your recent sessions with their ratings.
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
  project's `.venv`, and the output streams into the feed.
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
**Fix what it found** sends its findings back to the session's plan, which
fixes the real problems and says why it skips any it disagrees with.

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
  - a resting plan is never offered by default.
- Real Chromium: the whole page against the real backend, with demo tools, on
  desktop and phone, with no horizontal overflow and no console errors.
