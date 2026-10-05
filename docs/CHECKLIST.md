# Your checklist: turning "tested" into "seen working"

**Dated 2026-10-04.** Everything here needs your PC, your voice or your
accounts, so it can't be done from the build machine. The items are in order:
quick and free first, then the ones that unlock the most.

**Each result you send back moves rows** in the
[capability matrix](CAPABILITY_MATRIX.md) from TESTED or NEEDS YOU to LIVE,
with the date. Send the line each item asks for, never a key or a token.

| # | Check | Time | Cost | Send back | Moves |
| --- | --- | --- | --- | --- | --- |
| 0 | Update | 5 min | free | nothing | — |
| 1 | The two free self-checks | 2 min | free | the two summary lines | sanity |
| 2 | MCP self-test | 2 min | free | its output | MCP server → LIVE |
| 3 | One Telegram message | 1 min | free | did it answer? | Telegram → LIVE |
| 4 | Task suite, live, per model | 10 min | a few cents | `--history` table | Task suite → LIVE; model choice by evidence |
| 5 | Demonstration, live | 5 min | a few cents | `pass: … fail: …` line | Demonstration → LIVE |
| 6 | Your own documents | 15 min | free | the summary line | Documents → LIVE |
| 7 | Speech model vs loudness | 15 min | API cost of 20 turns | `agent.voice_timing` output | Speech model, Celine's speed (Pillar 1) |
| 8 | Record the Alex voice | 5 min | free | does it sound like you? | Voices → LIVE |
| 9 | Two-hand pull, with counts | 10 min | free | grabs per hand out of 20 | Two-hand → LIVE |
| 10 | Relay on Railway | 30 min, once | Railway plan | `--check` output + "lid shut, phone answered?" | G2, phone while PC is off, local node |
| 11 | Car orb from the cloud | 10 min | — | does the orb talk in the car? | Car page → LIVE |
| 12 | Call Apex | 20 min | Twilio number + per-minute | did the call answer? | Call Apex → LIVE |
| 13 | Connect one app | 10 min | Composio plan | demo app steps' rows | Apps, demo step 4 → LIVE |
| 14 | Photoreal video avatar | 1 h, once | free | render time per sentence, and how it looks | Video avatar → LIVE |
| 15 | Live photoreal face (Simli) | 15 min | Simli per-minute | does it look real, and how long before it speaks? | Live face → LIVE |

---

## 0. Update

After the latest work is merged:

```cmd
cd /d C:\Users\alexk\Apex
git checkout main
git pull
.venv\Scripts\pip install -r requirements.txt
Setup-Apex-Speech-Model.cmd
```

The last line downloads the browser speech model (about 14 MB, checked
against its pinned hash). It should end with `[ok  ]`.

## 1. The two free self-checks

```cmd
.venv\Scripts\python scripts\apex_demo.py
.venv\Scripts\python scripts\apex_tasks.py
```

Expect `pass: 15 · fail: 0 · unknown: 1` and `2/2 tasks pass`. These use a
scripted model, so they only prove your install is whole. If either fails,
send that first: nothing below will be trustworthy until it passes.

## 2. MCP self-test

```cmd
.venv\Scripts\python scripts\apex_mcp.py --self-test
```

Send the whole output. If it's all OK, run `Setup-Apex-MCP.cmd` to plug Apex
into Claude Code or your other tools ([MCP_SERVER.md](MCP_SERVER.md)).

## 3. One Telegram message

With Apex running, send your bot "what's on my calendar tomorrow?". Tell me
whether it answered and roughly how fast.

## 4. Task suite, live

```cmd
.venv\Scripts\python scripts\apex_tasks.py --live
.venv\Scripts\python scripts\apex_tasks.py --live --model deepseek-flash
.venv\Scripts\python scripts\apex_tasks.py --history
```

Add one `--live --model …` line per model you want to compare. Send the
`--history` table ([TASKS.md](TASKS.md)).

## 5. Demonstration, live

```cmd
.venv\Scripts\python scripts\apex_demo.py --live
```

Send the `pass: … fail: …` line and any FAIL rows ([DEMO.md](DEMO.md)). The
app steps say UNKNOWN until item 13.

## 6. Your own documents

1. Put about 10 real files in one folder: Word, PowerPoint, Excel, two PDFs
   with tables, and one scan.
2. Add a `facts.json` listing a few phrases from each file
   ([DOCUMENTS.md](DOCUMENTS.md) shows the format).
3. Run:

```cmd
.venv\Scripts\python scripts\doc_acceptance.py C:\path\to\that\folder
```

Send the summary line, and the names of any files that failed.

## 7. Speech model vs loudness

Start Apex with `Start-Apex-Celine-Fast.cmd`, open the companion and press
Ctrl+F5. Use headphones.

1. Settings → **Hands-free detects: Loudness**. Turn on hands-free and do
   about 10 normal spoken turns.
2. Switch to **Speech (model)** and do about 10 more.
3. Run:

```cmd
.venv\Scripts\python -m agent.voice_timing
```

Send the output. It shows the time until the transcript comes back for both
detectors, side by side, and Celine's overall speed (Pillar 1)
([HANDS_FREE_COMPANION.md](HANDS_FREE_COMPANION.md)).

## 8. Record the Alex voice

Dashboard → **Voices** → type `Alex`, press **Record** and read the script out loud (20–30 seconds is best).
Then pick it and ask something. Tell me whether it sounds like you
([VOICES.md](VOICES.md)).

## 9. Two-hand pull, with counts

On `/board`, try 20 two-hand pulls. Count how many grab and hold on each
hand. Send the two numbers, for example "left 18/20, right 19/20". If you can,
also record them with **Record my gestures** in the HANDS panel, and send the `recordings\gestures-<time>.json.gz` file ([BOARD_MOVES.md](BOARD_MOVES.md)).

## 10. Relay on Railway

Follow [RELAY_RAILWAY.md](RELAY_RAILWAY.md), steps 1–6. Then:
1. Run `.venv\Scripts\python -m agent.relay --check`; every line should say `[ok  ]`.
2. Shut the laptop lid, and ask something from `https://<your-relay>/phone`.

Send the `--check` output, and whether the phone answered with the lid shut.

## 11. Car orb from the cloud

On the Railway Apex service, set `DASHBOARD_TOKEN`, an AI key and
`OPENAI_API_KEY` ([CAR_AND_SPATIAL.md](CAR_AND_SPATIAL.md)). Then open
`/drive#orb` on the phone in the car, tap once and talk. Does the orb answer?

## 12. Call Apex

Do this after item 10. Follow [CALL_APEX.md](CALL_APEX.md): Twilio number,
relay variables, then the webhook. Check Twilio's prices and your
international calling cost first. Then press the steering-wheel phone button
and say "Call Apex". Did it answer, with the PC on and with it off?

## 13. Connect one app

Dashboard → **Apps**: add your Composio key and sign in to one app, for
example Notion. Then run the demonstration with that app's tools
([DEMO.md](DEMO.md), "Adding the app steps"). Send the app rows of the report.

## 14. Photoreal video avatar

Follow [VIDEO_AVATAR.md](VIDEO_AVATAR.md):
1. Install with `Setup-Apex-Video-Avatar.cmd`.
2. Make the idle video.
3. Start it with `Start-Apex-Video-Avatar.cmd`.
4. Choose **Video (photoreal)** and talk.

Send:
- the avatar server's console output, which includes the render times;
- one sentence on how real it looks.

## 15. Live photoreal face (Simli)

Follow [LIVE_AVATAR.md](LIVE_AVATAR.md):
1. Create a Simli account and a face.
2. Put the key and face ID in `.env` with `set_env_key.py`.
3. Choose **Live face (Simli)**, and talk with Local Qwen.

Send back:
- how real it looks;
- roughly how long before it starts talking;
- the setting's message, if it falls back to the orb.
