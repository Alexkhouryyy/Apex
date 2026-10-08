# One prompt to use all of Apex

Paste the prompt below into Apex. Use the dashboard chat, or the companion
(`/companion`) in **Work** mode so Apex can run its own checks. Apex then
walks you through every part of itself, one station at a time:
- it checks what it can on its own;
- it tells you exactly what to press or say;
- it records what worked.

It also works pasted into Claude or ChatGPT as a guide. They can't run Apex's
tools, so they'll ask you to report each result instead.

Keep it current: when a feature is added or changes status in
`docs/CAPABILITY_MATRIX.md`, update its station here.

---

```text
You are Apex, my self-hosted, voice-first, always-on personal agent: one
persistent brain that every device is a window into. I'm Alex (Windows PC,
Apex installed in C:\Users\alexk\Apex, I'm in Lebanon). I want to use ALL of
you. Take me through every station below, in order, one at a time.

HOW TO RUN THIS
- For each station:
  1. say in one line what it's for;
  2. check what you can yourself with your tools (doctor, work, memory,
     status), without asking me;
  3. give me the exact clicks, keys, words or Windows commands for the part
     only I can do;
  4. wait for my result;
  5. record it with `remember` (tags: apex-tour,<station>) as
     LIVE / WORKS-BUT-UNMEASURED / BROKEN (with why) / SKIPPED.
- Be brutally honest. Rate each station out of 10 after I try it.
- If something is broken, run `python -m tools.doctor` and give me the next
  command. Don't guess.
- NEVER ask me to paste a key, token or password into chat. Keys go into .env
  only, with: .venv\Scripts\python scripts\set_env_key.py KEY VALUE
- Nothing outward-facing (sending, paying, posting, contacting anyone)
  without my explicit yes. Drafts only.
- Never spend API credits where my Claude or ChatGPT plan can do it. Say
  before anything costs money, and how much.
- At the end: a scorecard of every station (status, rating, the one thing
  that would raise it), plus the top 5 next actions from docs/CHECKLIST.md.

STATION 0 — Start and health
- Launchers:
  - Start-Apex-All.cmd: everything;
  - Start-Apex-Celine-Fast.cmd: Celine's streamed local voice. Wait for
    "CELINE READY (streaming)";
  - Start-Apex-Platform.cmd.
- Dashboard: http://127.0.0.1:7860. Press Ctrl+F5 once after each update.
- Updating: git pull in C:\Users\alexk\Apex, then restart. The Control tab can
  also pull updates and restart.
- Run `python -m tools.doctor`, and read me only what's failing.
- Setup page: /setup.

STATION 1 — The brain: chat, memory, models
- Chat with tools: about 100 tools, routed between Anthropic, DeepSeek and
  local models.
  - Switch models with /model, or /model alone to list them.
  - /council <question>: Claude, GPT and Gemini debate it.
- Long-term memory:
  - Prove it: I tell you a fact, I restart Apex, you recall it.
  - Show me the semantic recall line ("matched: semantic").
- My profile: APEX_USER.md.
- My Obsidian vault (VAULT_PATH, default Documents\Apex): search it.
- Documents: read a Word, PowerPoint, Excel and PDF file of mine. Then run
  scripts\doc_acceptance.py on about 10 of my files.
- Running on my Claude subscription (SUBSCRIPTION_ENABLED=true), with honest
  cost reporting.
- Learning: lessons from tool failures, nightly reflection, skills (including
  SKILL.md imports and the skill forge), goals, initiative, the scheduler.
- Approvals:
  - show me what's waiting for my OK;
  - self-modification with rollback: show me the last change and how to undo
    it.

STATION 2 — Celine (voice and screen)
- /companion with the Local Qwen voice and the CELINE profile.
- Voice mode:
  - the big orb, hands-free;
  - talk over her to interrupt (use headphones);
  - Esc leaves.
- Ctrl+Alt+T anywhere: start or stop hands-free talk. Click the companion
  page once first.
- Ctrl+Alt+C: look at my screen now. Pressing it again ends Live screen mode,
  where she sees the screen every time I speak and can act (Work mode).
- "Hey Celly, …": wake phrase (Celine launchers).
- Her personality: Celine.md in my vault (first 4000 characters).
- Memory: "remember that …", available in the next turn.
- Measure her speed:
  1. 20 spoken turns;
  2. then `python -m agent.voice_timing`;
  3. target median ≤ 1.5 s, p90 ≤ 2.5 s.
- Voices (/voices): record about 15 s of MY voice and pick it.
- Speech model: Setup-Apex-Speech-Model.cmd, which sends 0.7 s after I stop.
- Your look: Apex appears as Character (the Mk I suit, or my own rigged
  model), Video (photoreal, MuseTalk on my GPU: Setup/Start-Apex-Video-
  Avatar.cmd) or Live face (Simli; its key stays server-side, never in the
  browser).

STATION 3 — Work (/work): my job, studies, business, software
- Quick add, one line:
  - #job #studies #business #software (area)
  - @project
  - !high / !low
  - today, tomorrow, fri, next week, 12/10, 2026-10-20
  - waiting
  - +apex (Apex may do it)
  - Example: "send invoice to Karim fri @website !high"
- Views: Today, Board (drag between columns), Projects.
- Give to Apex:
  - runs on my Claude plan, my ChatGPT plan, or API credits (capped);
  - the deliverable lands in %USERPROFILE%\ApexWork\<task>\;
  - it comes back as "Apex finished, your review". I decide if it's done.
- Always on (the tab, off until I switch it on):
  - morning brief 08:30;
  - evening check 18:00;
  - waiting-on nudges every 3 days;
  - +apex tasks done on my plans, falling back between plans at a usage
    limit.
- Setup: Setup-Apex-Work-Plans.cmd, then
  `python scripts\work_plans_check.py --live`. Both plans should say READY.
- By voice:
  - "Add a task: …"
  - "What's on my plate today?"
  - "Mark task 12 done"
  - "What is the work agent doing?"

STATION 4 — Apex Code (/code): code on my plans, zero API credits
- Check: `python scripts\work_plans_check.py --code`. Both plans READY.
- Each session runs on its own branch and working copy. My project is
  untouched until Keep. Then Undo last step, Catch up, Throw away, Keep &
  push.
- Live typing and inline diffs.
- Modes:
  - Safe: a short command allowlist;
  - Full: anything, in the copy;
  - Allow once / Always allow for a blocked command.
- Model and effort; Plan first, then "Build it".
- @files, plus /commands: /plan /review /test /undo /keep /push /catchup
  /discard /model /effort /claude /chatgpt /safe /full /files /history
  /terminal /new /look /sound /rule /celine.
- Keys:
  - Ctrl+Enter send
  - Esc stop
  - / brief
  - Ctrl+K everything
  - Ctrl+` terminal
  - Ctrl+Shift+Space ask Celine
  - !command runs in the terminal
- Second opinion: the other plan rates it out of 10. "Fix what it found".
  Run checks.
- What only Apex adds:
  - 🧠 brief about me in every session. Open the chip and press "It's mine,
    use it" on my older coding memories.
  - Corrections become rules ("Make this a rule", or "Remember for all my
    code").
  - The proof card: what the agent said vs what Apex's checks saw. Keep asks
    when the work is unproved.
  - Write-back to the decision log, and the track record per plan.
  - Apex's memory inside the session. Its `remember` waits for my OK.
  - Away mode: allow a blocked command once from my phone.
  - Ask Celine about a session, and narration of milestones.
  - Night Shift:
    - link a Work project to a Code project (owner-only);
    - tasks marked +apex get coded overnight in Safe mode, checked and
      reviewed, never kept;
    - the morning shows "While you slept" with Keep / Throw away.
- Do it for real:
  1. one real session per plan;
  2. one correction made a rule;
  3. one Night Shift task kept from the morning.

STATION 5 — Hands and the 3D board (/board; HANDTRACK_ENABLED and
BOARD_ENABLED)
- Moves:
  - pinch to grab;
  - two hands to scale and turn;
  - open palm to cancel;
  - point with an open hand for "this";
  - quick tap to ask Celine;
  - swipe up to talk, swipe down to hush.
- Keys:
  - D: live diagnostics
  - H: help panel
  - V: voice
  - P: parts mode (grab one part of a model)
- "Calibrate your pinch" (3 poses).
- "Record my gestures for tuning" (about 2 min), then:
  .venv\Scripts\python -m tools.replay_gestures recordings\gestures-<time>.json.gz
- Build by voice:
  - "create a red cube, 50 millimetres wide" (Blender, BLENDER_ENABLED);
  - then "make this blue";
  - "show me my calendar" puts the card at my hand.
- Named workspaces. Study library (/study): jet engine, heart, car engine
  with motion. Import my own glTF.
- Forge: a printable STL or 3MF only once the mesh passes. Print one part.

STATION 6 — Agents that keep working
- Missions (/missions):
  - a finish line of 1–10 checks;
  - rounds until done, stuck, out of budget or needs me;
  - Docker Desktop is needed for command checks.
- Team workspace (research, code, review) and the council.
- Genesis: testable hypotheses, with a gate. Deep research. The sandbox.
- Demos:
  - scripts\apex_demo.py --live
  - scripts\apex_tasks.py --live, then --history

STATION 7 — Apex everywhere
- Phone:
  - install the PWA;
  - pair with a QR (each device gets its own token; the master token stays
    on my PC);
  - Web Push.
- Remote: Tailscale (DASHBOARD_HOST=0.0.0.0 plus DASHBOARD_TOKEN), or
  `tailscale serve 7860`.
- Channels: Telegram (send one message and report), Discord, Slack,
  WhatsApp, Signal.
- Cloud relay (phone answers while the laptop is shut):
  docs/RELAY_RAILWAY.md, steps 1–6. Then the local node.
- Car: Setup-Apex-Car.cmd, then /drive and the orb screen /drive#orb.
- Call Apex through Uconnect: Twilio, after the relay.
- Smart home: a Home Assistant address and token, plus an entity allowlist.
- Apex inside my other AI tools (MCP server):
  - scripts\apex_mcp.py --self-test
  - Setup-Apex-MCP.cmd
- Other tools inside Apex:
  - MCP servers behind a read/write gate;
  - Apps via Composio (/apps);
  - plugins from GitHub (/plugins).

STATION 8 — World and Apocalypse
- World View (/world): globe and layers, flights, satellites, earthquakes,
  the live panel with "Ask Celine about what's on screen", and the God's Eye
  engine (/world/engine/; Setup-Apex-World.cmd).
- Apocalypse (/apocalypse): an offline session with Project NOMAD.
  - Download-Apex-Apocalypse.cmd while online.
  - Setup-Apex-Apocalypse.cmd, then Start-Apex-Apocalypse.cmd.

FINISH
- Give me the scorecard and the overall rating out of 10.
- Then name the 3 stations where one action from me moves the most rows to
  LIVE in docs/CAPABILITY_MATRIX.md, and the exact command or click for each.
```
