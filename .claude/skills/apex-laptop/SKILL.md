---
name: apex-laptop
description: Give the Apex owner exact, copy-paste Windows PowerShell for their laptop (C:\Users\alexk\Apex) — updating after a merge, starting/stopping/restarting Apex, Celine's voice or the desktop Companion, reading logs, setting .env keys, checking disk/GPU/memory — and diagnose what they paste back from it (Celine voice crashes: CUDA out of memory, paging file error 1455, a full C: drive). Use whenever the owner asks for a command, "cmd prompt", "powershell plz", "how do I run/restart/update", says something "turned off" or crashed, or pastes laptop terminal output.
---

# Apex on the owner's laptop

The owner runs Apex on a Windows laptop, in Lebanon. They work in **PowerShell**
and paste the output back. They want commands they can copy and run as-is, one
block at a time, with no placeholders to fill in.

## Facts about this machine

| What | Where |
|---|---|
| Apex folder | `C:\Users\alexk\Apex` |
| Apex's Python | `.\.venv\Scripts\python.exe` (inside the Apex folder) |
| Dashboard | http://127.0.0.1:7860 (Companion at `/companion`) |
| Settings | `.env` in the Apex folder. Change with `scripts\set_env_key.py KEY value` |
| Companion logs | `%LOCALAPPDATA%\Apex\Companion\desktop.log`, `startup.log` |
| Companion state | `%LOCALAPPDATA%\Apex\Companion\` (`follow.json`, `permissions.json`) |
| Offline library | `D:\Apex-Apocalypse` (`APEX_APOCALYPSE_HOME`) |
| GPU | NVIDIA. Celine's voice model is ~3.6 GB on it |
| RE tools | Node.js (`npx.cmd`), Ghidra 12.1.4 under `C:\tools`, JDK 21 |

C: has been nearly full before (about 1 GB free), which breaks voice loading
and updates. Check it whenever something big fails.

## Rules for writing commands

- Always start with `cd C:\Users\alexk\Apex`. The owner opens fresh windows.
- Run `.cmd`/`.bat` files with `.\`, e.g. `.\Start-Apex-All.cmd`.
- Use `npx.cmd`, not `npx`: PowerShell's execution policy blocks `npx.ps1`.
  Likewise use `curl.exe`, not `curl`, which is an alias for Invoke-WebRequest.
- Use `.\.venv\Scripts\python.exe`, never bare `python`, which may be a
  different Python.
- Use `-X utf8` for scripts that print progress, like the library downloader.
- Right-click "Run as administrator" hasn't worked for them. When admin is
  really needed: `Start-Process powershell -Verb RunAs`, then run the commands
  in the new window.
- One code block per step. Say in one line what each step does and what they
  should see if it worked.
- Never ask them to edit files by hand when `set_env_key.py` or a script can do it.

## Everyday commands

**Update after a merge**

```powershell
cd C:\Users\alexk\Apex
git pull
```

If `git pull` complains about local changes: `git stash`, then `git pull`, then
`git stash pop`. Explain what each one does before giving them.

**Start everything** (Apex + Celine's fast voice, in one console that must stay open)

```powershell
cd C:\Users\alexk\Apex
.\Start-Apex-All.cmd
```

**Restart.** If Apex was started with `Start-Apex-All.cmd`, press Ctrl+C in that
console (or close it), then start it again. That console also runs the voice
server, so closing it stops both. Otherwise, stop the background Apex first:

```powershell
cd C:\Users\alexk\Apex
.\stop-apex.bat
.\Start-Apex-All.cmd
```

Resident log: `Get-Content "$env:USERPROFILE\.apex\resident.log" -Tail 40`.

Other launchers: `.\start-apex.bat` (Apex only, in the background),
`.\Start-Apex-Celine-Fast.cmd` (just the voice server),
`.\Download-Apex-Library.cmd 90` (offline library; see `apocalypse-library`).
If the console closed ("it turned off"), give them the start command again.
Downloads and installs pick up where they stopped.

**Change a setting**

```powershell
cd C:\Users\alexk\Apex
.\.venv\Scripts\python.exe scripts\set_env_key.py COMPANION_FOLLOW_CURSOR false
```

Then restart Apex.

**Companion log (last 40 lines)**

```powershell
Get-Content "$env:LOCALAPPDATA\Apex\Companion\desktop.log" -Tail 40
```

**Health checks**

```powershell
'C: free GB: {0:N1}' -f ((Get-PSDrive C).Free/1GB)
'D: free GB: {0:N1}' -f ((Get-PSDrive D).Free/1GB)
nvidia-smi
Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 10 Name, @{n='MB';e={[int]($_.WorkingSet64/1MB)}}
```

## When Celine's voice crashes at startup

Read the pasted output and match it:

- **`CUDA out of memory`, though the GPU shows memory free**: Windows refused
  one large block. The voice server already retries, loading piece by piece. If
  it still fails, have them run `nvidia-smi`, close whatever else is using the
  GPU, and start again.
- **`The paging file is too small` / `error 1455`**: Windows ran out of virtual
  memory loading the model. The fix (from `docs/CELINE.md`):
  1. Free C: first. The page file needs 16–32 GB free there.
  2. Win+R → `sysdm.cpl` → Advanced → Performance Settings → Advanced →
     Virtual memory → Change → tick *Automatically manage* (or set C: to
     16384–32768 MB) → Set → OK → restart the PC.
  3. Until then, closing big programs helps. `vmmem` (WSL/Docker) holds a
     lot: `wsl --shutdown` frees it.
- **A full C: drive**: help them find what to delete, safest first: the
  Recycle Bin; `%TEMP%`; old downloads; pip's cache
  (`.\.venv\Scripts\python.exe -m pip cache purge`); Hugging Face caches they
  no longer use; moving big folders to D:. Show sizes before deleting anything:

  ```powershell
  Get-ChildItem $env:USERPROFILE -Directory -Force -ErrorAction SilentlyContinue |
    ForEach-Object { [pscustomobject]@{Folder=$_.Name; GB=[math]::Round(((Get-ChildItem $_.FullName -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum/1GB),1)} } |
    Sort-Object GB -Descending | Select-Object -First 12
  ```

  Never suggest deleting Windows folders, `.venv`, the Apex folder, model
  folders Apex still uses, or `D:\Apex-Apocalypse`.

Anything else: ask for the last 30 lines of the console, or the log above, and
reason from the actual error. Don't guess.
