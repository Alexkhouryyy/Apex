# Celine

Celine is Apex speaking in her own voice and personality. When the companion's
voice is **Local Qwen** with the **CELINE** profile (or the default profile, when
a Celine launcher started Apex), each reply comes from her. Asked her name, she
says she is Celine, a version of Apex. She won't call you "sir", and she talks in
spoken sentences, without lists or headings.

Start Apex with **`Start-Apex-Celine-Fast.cmd`** and wait for
`CELINE READY (streaming)`. Open `http://127.0.0.1:7860/companion` and press
**Ctrl+F5** once after each update.

---

## Voice mode: talk only

Press **Voice mode** at the top of the companion. The page shrinks to a large
orb, a status line and captions showing your last message and her last reply.
The page switches to:

- Celine's voice (the CELINE profile);
- spoken replies;
- hands-free listening, which sends your speech after a pause and listens again
  once she has finished.

**Stop** or **Esc** leaves Voice mode. It also stops listening and puts your
earlier voice settings back. The full conversation is still there when you
come out.

Voice mode doesn't open when it can't work. If there's no microphone, or
Celine's voice server isn't running, the page says why and stays as it was.

**Talk over her to interrupt.** Start speaking while she's talking, or while
she's still working out her answer, and she stops. What you say is sent as
your next message. The simulated check measures 0.25 s from the start of your
voice to her stopping (the plan's limit is 0.3 s). On your laptop it hasn't
been measured yet: the line under the message box shows the real number each
time.

- A short noise (a cough, a door) doesn't interrupt. It takes 0.2 s of voice
  at three times the microphone's normal threshold.
- **Use headphones.** Through speakers her own voice reaches the microphone.
  The browser's echo cancellation removes most of it, but not always all. If
  the words it heard are mostly the ones she was saying, it's treated as her
  echo: nothing is sent and the page says so. She has already stopped by then,
  though, so without headphones she can cut herself off. If that happens, turn
  off **Interrupt by talking** in settings, or set microphone sensitivity to
  *Low*.

## Talk from anywhere: Ctrl+Alt+Space

Press **Ctrl+Alt+Space** in any window and Voice mode starts in the open
companion page: she listens hands-free. Press it again to stop. If she's in
the middle of a reply, the press cuts her off and she listens.

- The companion page must be open, the same as for Ctrl+Alt+C.
- **Click that page once** after opening it. A key pressed in another window
  doesn't count as using the page, and the browser keeps the mic and sound
  shut until the page has been clicked. Until then the page tells you to
  click it.
- With the companion tab and the board both open, each press goes to one of
  them, not both.
- Change the key with `CELINE_TALK_HOTKEY` in `.env`, or leave it empty to
  turn it off.

## Memory

Each companion turn includes what Apex remembers: the most recent memories,
plus the ones most related to what you just said. The companion used to start
every conversation knowing nothing. It now uses the same long-term memory as
the rest of Apex.

To save something, just tell her, for example *"remember that my dentist is
Tuesday at 3pm"*. She saves it with the `remember` tool, including in Discuss
mode. A memory saved in one turn is available in the next.

## Look now: Ctrl+Alt+C or "Hey Celly"

> **Fixed 2026-09-25:** until this date Ctrl+Alt+C never actually bound.
> The code asked for a hotkey class that doesn't exist, and the console
> printed "Hotkeys not available" instead. The tests had replaced that part
> with a stand-in, so they didn't catch it. They now go through the real
> class.

Wherever you are on the laptop, press **Ctrl+Alt+C**. Apex captures the screen
the mouse is on at that moment, and Celine says what she sees and suggests the
most useful next step.

You can also say **"Hey Celly"** and then your question: *"Hey Celly, why is
this test failing?"* The words after the wake phrase become the question. If
you say only "Hey Celly", she answers the same way as the hotkey.

- **The companion page must be open.** It is where she answers from. If no
  page is open, the Apex console says so. A capture waits for two minutes, so
  opening the page straight afterwards still works.
- If she is in the middle of saying something, she stops and looks.
- The screenshot goes from Apex straight to the model. It never passes
  through the browser, and each capture is used once.
- Snapshots go to your selected AI provider, just like a shared screen.

The wake phrase is turned on by the Celine launchers
(`Start-Apex-Celine.cmd`, `Start-Apex-Celine-Fast.cmd`). It listens with
Whisper on the laptop, all the time, and costs some CPU. Settings in `.env`
take precedence:

| Setting | Default | Meaning |
|---|---|---|
| `CELINE_HOTKEY` | `<ctrl>+<alt>+c` | The hotkey. Leave it empty to turn it off. |
| `CELINE_WAKE_ENABLED` | `false` (`true` from the Celine launchers) | Listen for the wake phrase. |
| `CELINE_WAKE_PHRASES` | `hey celly, hey celli, hey cely, hey selly, …` | Also covers the ways Whisper tends to mishear "Hey Celly". |

If "Hey Celly" is misheard as something that isn't on the list, add the
version Whisper produced to `CELINE_WAKE_PHRASES`.

## Her personality is yours to write

Create `Celine.md` in your vault folder (`VAULT_PATH`, by default
`Documents\Apex`) and describe how she should be: tone, humour, how long her
answers are, what she should never do. Your text replaces the default
personality from the next reply onwards, and no restart is needed. Only the
first 4000 characters are used.

Her identity isn't part of that file. She is always Celine, a version of Apex,
because a cloned voice that introduces itself with a different name is the
confusion this feature exists to prevent.

## Honest limits

- **Look now needs a desktop session.** It uses `mss` and `pynput`, which are
  both in `requirements.txt`. On a headless machine the console prints why it
  isn't available.
- **Wake-word accuracy hasn't been measured on your microphone.** The phrase
  list is a guess at Whisper's mishearings. Tell me what it hears and the list
  will follow.
- **"Is this Celine?" is decided per turn** from the selected voice. Replies
  in the page are labelled CELINE when the CELINE profile is chosen. With
  "Apex default voice" the label still reads APEX, even though the server
  answers as Celine when the launcher set her as the default.
