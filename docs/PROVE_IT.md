# Prove it — the checks only you can run

Everything here needs your laptop, your voice, your phone or your cloud
server, so it cannot be proven in a test. Each check says exactly what to
send back; the result goes into `docs/APEX_V2_PLAN.md` as a measured number,
not a claim.

Run the commands in a Command Prompt in the Apex folder.

---

## 0. Update (2 minutes)

```cmd
git pull
Apex.bat
```

Open the companion at `http://127.0.0.1:7860/companion` and press **Ctrl+F5**
once, so the browser loads the new page. In its settings you should see
**Speak as it writes** and **Start on the first phrase**, both ticked. If
not, reload again.

---

## 1. Voice — Pillar 1 (about 15 minutes)

Start Apex with **`Start-Apex-Celine-Fast.cmd`** (streamed Celine — see
`docs/LOCAL_QWEN.md`) and wait for `CELINE READY (streaming)`.

**Before:** untick **Speak as it writes**. Tap **Talk** and ask Celine 10
ordinary questions — a mix of short ones ("what's the time"), long ones
("explain how the relay works") and one that needs a tool ("what's on my
calendar tomorrow"). After each, a line under the message box shows the
timing.

**After:** tick **Speak as it writes** again. The same kind of 10 questions.

Then:

```cmd
.venv\Scripts\python -m agent.voice_timing
```

**Send back:**
- the whole output (it prints both modes side by side and says how much
  earlier — or LATER — the first sound came);
- one sentence on how **Start on the first phrase** sounds: does Celine
  pausing at the first comma sound natural, or like she stopped mid-thought?

---

## 1b. Celine — Voice mode and Look now (5 minutes)

Details: `docs/CELINE.md`. With `Start-Apex-Celine-Fast.cmd` running and the
companion open:

1. Press **Voice mode**. Ask *"what's your name?"*, then *"remember that my
   favourite colour is green"*, then *"what's my favourite colour?"*
2. Press **Esc**. The chat comes back.
3. Open any window (code, a web page) and press **Ctrl+Alt+C**. Then say
   *"Hey Celly, what am I looking at?"*

**Send back:**
- what she answered to her name;
- whether she remembered the colour;
- whether Ctrl+Alt+C and "Hey Celly" each worked. If "Hey Celly" did
  nothing, paste the Apex console lines from that moment.

---

## 2. The relay — gate G2 (about 30 minutes, once)

Follow `docs/RELAY_DEPLOY.md` Parts A to C on your cloud server. Then:

```cmd
.venv\Scripts\python -m agent.relay --check
```

**Send back:** the whole `--check` output.

Then the real test:

1. With Apex running, tell it something specific — *"my dentist is Tuesday at
   3pm"* — and wait a minute.
2. **Shut the laptop lid.**
3. On your phone, on mobile data, open `https://YOUR-NAME.ts.net/phone` and
   ask *"when is my dentist?"*

**Send back:** a screenshot of the phone page with the answer — or of
whatever it said instead.

---

## 3. Hands — gate G1 again, with numbers (10 minutes, optional)

G1 was recorded as passed without counts. If you want it recorded as a
number: open `http://127.0.0.1:7860/board?diag=1&token=YOUR_TOKEN`, do 20
grabs with each hand, and count the drops.

**Send back:** `right __/20, left __/20`.

---

## If something goes wrong

Paste the exact error or a screenshot. Do not work around it — a workaround
hides the thing that needs fixing.
