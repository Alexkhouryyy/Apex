# Reverse: see how software works

Open **Reverse** from the dashboard sidebar, or go to `/reverse`.

Point Apex at a program, app, firmware image or code bundle **you own or are
allowed to examine**, and it takes the file apart with
[REA](https://github.com/morluto/rea) (Reverse Engineer Anything, MIT): the
functions inside it, the text it contains, what it imports, and the decompiled
code of any function. Apex never runs the program and never changes the file.

This is for lawful work: software you own, interoperability, learning, security
research, software you're authorised to analyse. It is not for cracking,
licence bypasses, or anything you have no right to inspect — REA's own terms say
the same, and Apex passes that on.

## The page

- **Open a file.** Give a path to a copy on your own drive (not a system
  folder). The first open imports the file into Ghidra and can take a minute;
  after that, the session stays open so moving around is quick. At most two
  files are open at once, and an untouched one closes itself after 30 minutes.
- **Overview.** What the file is: format, architecture, entry point, how many
  functions, its segments.
- **Functions.** The list of functions, searchable. Click one to see its
  **decompiled code**, its assembly, what it calls and what calls it.
- **Strings.** The readable text inside the file, searchable — often the
  fastest way to find where something happens (a message, a URL, a filename).

To understand a function in plain words, copy its decompiled code into Apex
chat or ask Celine; the Reverse page itself only reads.

## Why it's safe

- **Owner only.** Every Reverse route needs the master dashboard token, reading
  included, and opening or closing a file needs a same-site request. A paired
  phone or another device can't reach it.
- **Read only.** Apex calls only REA's reading tools (an allowlist in
  `agent/reverse.py`). Anything that would run the program, capture it, install,
  push, annotate or write is refused before it reaches REA, so an inspection
  can never turn into an action. REA opens the file in a throwaway Ghidra
  project; the file on disk is never written.
- **Your own drives only.** Apex refuses a path in a system folder (Windows,
  Program Files, /etc, /usr and the like) or off your own drives, and refuses an
  empty file or one larger than 2 GB.
- **A binary's own text is untrusted.** The page shows every string and name as
  plain text, never as HTML.

## Set it up (once)

1. **Node.js 22.19 or newer** (`node -v`). REA runs through `npx`.
2. Add REA once: `npx rea-agents@6.4.0 setup` (the same package Apex Code uses,
   pinned by `CODE_REA_PACKAGE`). Reopen the page.
3. **For Windows `.exe`/`.dll` files** you also need **Ghidra** and a **JDK 21**,
   and Apex must know where they are (`GHIDRA_INSTALL_DIR`, `JAVA_HOME` in
   `.env`; `scripts/set_env_key.py` sets them). JavaScript/Electron apps,
   websites, .NET and Android analysis work without Ghidra. REA's Windows Ghidra
   support is read-only and new.

## Honest limits

- **Not yet seen on a real binary through this page.** The workspace is covered
  by tests that drive a fake REA over a fake file (`tests/test_reverse.py`,
  `scripts/check_reverse_ui.cjs`); REA's exact result shapes are read leniently
  and confirmed only by a real session on your PC. On your laptop,
  `npx rea-agents@6.4.0 providers --json` already reports Ghidra 12.1.4 and
  JDK 21 as available.
- The page reads; it doesn't rename, comment or save analysis (those are off on
  Windows REA anyway).
- A REA process left open is bounded by the 30-minute idle close and by Apex's
  own restart; it isn't killed the instant Apex stops.
