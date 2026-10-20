---
name: apocalypse-library
description: Run, resume, size and extend Apex's offline "Apocalypse" library (Project NOMAD — Kiwix ZIM books and Wikipedia, worldwide PMTiles map, Kolibri courses, local AI) on the owner's laptop, read the downloader's progress output, and add new offline packs safely. Use when the owner pastes download progress, asks "how much left", "another 30 GB", "continue the download", says the download stopped or the window closed, wants more offline resources (medicine, Arabic, survival, repair, courses), or asks about the Apocalypse page or NOMAD readers.
---

# The Apocalypse offline library

Apex's offline mode is built on Project NOMAD (pinned source in
`integrations/project-nomad-source.zip`). It is prepared online and used offline.
Full reference: `docs/APEX_APOCALYPSE.md`. The downloader is
`scripts/prepare_apocalypse_library.py`; the one-click launcher is
`Download-Apex-Library.cmd`. For PowerShell conventions, see the `apex-laptop`
skill.

## Facts

- Library folder: `D:\Apex-Apocalypse` (`APEX_APOCALYPSE_HOME`, defaulted by the
  launcher). Books go to `nomad\storage\zim`, the map to `nomad\storage\maps\pmtiles`.
  Nothing large goes on C:, which is nearly full.
- The plan is `D:\Apex-Apocalypse\english-worldwide-plan.json`. It pins every
  archive's version, exact size and publisher checksum, and records progress.
- The full selection is about 330 GiB. Rough tiers for the launcher's size
  prompt: **90** = books, guides, medicine, TED; **220** = plus full English
  Wikipedia with images; **360** = everything, including the worldwide map. The
  number is a total that counts what's already downloaded. "Another 30 GB" means
  the current total plus 30.
- A run refuses to start unless the selected remainder fits with **20 GiB free
  headroom** on D:.
- Sizes: `--max-download-gb` is decimal GB (1 GB = 10⁹ bytes); plan totals print
  in GiB. Mention the difference when the numbers look inconsistent.

## Download / resume (the normal case)

```powershell
cd C:\Users\alexk\Apex
git pull
.\Download-Apex-Library.cmd 90
```

Equivalent: `.\.venv\Scripts\python.exe -X utf8 scripts\prepare_apocalypse_library.py --max-download-gb 90 --until-done`.

Tell the owner how it behaves, because it's what they care about:

- **Resumes, never restarts.** Finished archives are skipped in seconds (not
  re-hashed unless changed; `--verify-all` forces a full re-check). A half-done
  one continues from its `.part` file, even when the resume lands on a
  different Kiwix mirror.
- **Restarts and power cuts:** the launcher adds `Apex-Library-Resume.cmd` to the
  Startup folder, so it carries on by itself at the next sign-in, and removes it
  when the download is done. Every 10 s the partial is forced to disk and its
  safe length recorded (`durable_bytes`). After a power cut, any garbage past
  that is cut off and re-fetched (at most ~10 s of download).
- To stop auto-resume: delete
  `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Apex-Library-Resume.cmd`.
- **Rides out internet drops.** Each archive retries 6 times. In `--until-done`,
  a longer outage waits 30 s, then doubling, at most 10 minutes, and keeps going.
- **Stops on what a retry can't fix** (checksum mismatch, full drive), names the
  archive, and keeps every file.
- It asks Windows not to sleep. Closing the laptop lid can still override that.

### Reading pasted progress

- `Wikipedia: 45.3% · 57.7 GB of 127.4 GB · 1.20 MB/s · about 16 h left` is
  printed every 10 s. Speed is averaged since this connection started.
- `the connection dropped at …% … Resuming in N s` is normal on a bad line.
  `The internet is down or unstable. Waiting N s` means it's riding out an outage.
- `Drive D: isn't connected` / `[WinError 3] … 'D:\\'` (older versions): the
  external ADATA drive isn't connected. Nothing is lost. Have them plug it in,
  then check its letter with
  `Get-Volume | Where-Object DriveLetter | Select-Object DriveLetter, FileSystemLabel, @{n='Size GB';e={[math]::Round($_.Size/1e9)}}`.
  If it came back as another letter, set it back to D: (Win+X → Disk Management →
  Change Drive Letter and Paths), or for one window run
  `$env:APEX_APOCALYPSE_HOME = "E:\Apex-Apocalypse"` first. Never suggest formatting
  or "repair" prompts without seeing the message. In keep-going mode it waits
  for the drive by itself.
- `The mirror would restart … from zero; keeping …` means one mirror couldn't
  resume. It keeps the partial and tries again (normally another mirror).
- `the end of the saved file failed its check; downloading the last …` is the
  power-cut recovery working.

- `This batch: N archives, X GB` is what this run will fetch.
  `English + worldwide: M archives, Y GiB` is the whole plan.
- "How much left" = the batch size minus finished archives. Sum the sizes of
  the archives not yet reported complete, and say it in GB plus a rough time at
  the speed shown.
- `The remaining plan does not fit with 20 GiB headroom` → free space on D: or
  pick a smaller number. Show `'D: free GB: {0:N1}' -f ((Get-PSDrive D).Free/1GB)`.
- `Unknown archive selection` → a typo in `--only`/`--add`, or an ID that isn't
  in the plan.

## Add a new pack

Use `--add` with the Kiwix catalog name: the file name without
`_YYYY-MM.zim`, e.g. `wikivoyage_en_all_maxi` from
`wikivoyage_en_all_maxi_2026-01.zim`. Browse https://library.kiwix.org to find
names; `_maxi` = with images, `_nopic` = text only, `_mini` = intro sections only.

The plan already holds 59 archives (WikiMed, MedlinePlus, CDC, LibreTexts,
iFixit, Stack Exchange sites, Gutenberg, prepper guides, Wikipedia, the map...).
List them before suggesting anything, so you don't propose what's already there:

```powershell
(Get-Content D:\Apex-Apocalypse\english-worldwide-plan.json | ConvertFrom-Json).jobs | Select-Object id, status, @{n='GB';e={[math]::Round($_.bytes/1e9,2)}} | Sort-Object id
```

Then check the new name exists and see its size, without changing anything:

```powershell
curl.exe -s "https://library.kiwix.org/catalog/v2/entries?name=wikivoyage_en_all_maxi" | Select-String 'application/x-zim'
```

Then add and download it (repeat `--add` for several):

```powershell
cd C:\Users\alexk\Apex
.\.venv\Scripts\python.exe -X utf8 scripts\prepare_apocalypse_library.py --add wikivoyage_en_all_maxi --until-done
```

`--add` pins the pack's current version and checksum, downloads only that pack,
and leaves every other archive exactly as it was. **Never suggest
`--refresh-plan` to add a pack**: it re-resolves every archive to its newest
version, which can mean downloading hundreds of GB again. Add `--plan-only` to
queue a pack without downloading it now.

Choosing packs: prefer what works fully offline in a Kiwix reader, has a clear
license, and fills a real gap (offline medicine, first aid, Arabic-language
references for Lebanon, repair/how-to, survival, maps). For each suggestion give
its size and the `_maxi` vs `_nopic` trade-off. Medical references are source
material, not personal medical advice: say so when suggesting them.

Things that are **not** ZIM downloads and need NOMAD itself (Docker running,
`Setup-Apex-Apocalypse.cmd`): Kolibri courses (channel imports), the FDA drug
database (NOMAD ingestion), map fonts/styles, and AI/RAG embeddings. They stay
listed under the plan's `remaining_setup`. Don't call them installed.

## After downloading

Files on disk aren't readable until a NOMAD reader serves them:

```cmd
cd /d C:\Users\alexk\Apex
set "APEX_APOCALYPSE_HOME=D:\Apex-Apocalypse"
Setup-Apex-Apocalypse.cmd --nomad-readers
```

(Docker Desktop running, platform started with `Start-Apex-Platform.cmd`.)

If `Start-Apex-Platform.cmd` says **"An Apex service is already running"**, Apex
was started some other way (login autostart or `Start-Apex-All.cmd`). Don't make
the owner stop it. Start NOMAD alone, then run the readers step:

```powershell
cd C:\Users\alexk\Apex
$env:APEX_APOCALYPSE_HOME = "D:\Apex-Apocalypse"
.\Setup-Apex-Apocalypse.cmd --nomad-start
.\Setup-Apex-Apocalypse.cmd --nomad-readers
```

`--nomad-start` uses cached images only and never downloads. If it reports that
images are missing, NOMAD was never built on this PC. `--nomad` would download
and build them into Docker's storage on C:, which is nearly full, so check C:
space first.

Then open a book and an article to verify. A title in the catalog isn't proof the
library works offline: the real test is unplugging the internet and reading.

## Changing the downloader

It's careful code: resume by byte offset, checksum before "downloaded", never
deleting partials. Tests are in `tests/test_apocalypse_library.py`. Ship
changes with the `ship-apex` skill, including revert-audits.
