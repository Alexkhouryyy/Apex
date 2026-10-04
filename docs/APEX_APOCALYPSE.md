# Apex Apocalypse

## Resume a project after restarting

The Apocalypse screen has a project selector, saved conversation history and an editable handoff. It uses the same project records as Apex Home, with one offline conversation per project. Selecting a project here does not switch the shared spatial board. The conversation remains pinned while a turn runs. The local model can read or save the handoff through `project_checkpoint`; conflicting saves preserve the current version and require a reload.

Text replies and user requests are saved to the local SQLite database. A failed turn gets a fixed interruption marker; tools are not automatically replayed. Storage errors are reported rather than claiming a saved result. The local model receives a bounded recent window plus handoff excerpts; the page keeps the latest 200 full saved messages and the existing Chat history APIs retain older messages. File references in a handoff are context, not proof: read saved outputs again before claiming they exist or are correct.

Use **Use in chat** beside a text, Markdown or PDF document to prepare a request about that source. Send it when ready. Uploading archives still does not install Kiwix, courses or map readers.

`Start-Apex-Platform.cmd` starts the prepared normal and offline dashboards from one console. Its `--offline-only` option omits normal cloud Apex and Celine; `--no-nomad` skips Docker. It starts only cached NOMAD images and never resumes the large download plan. See `docs/start-apex.md` for ports, readiness and shutdown behavior.

A separate offline session for Apex, with Project NOMAD as the full offline
knowledge, education and map stack. Prepare resources while online; use what
you downloaded without internet. This does not turn internet services into
offline services and does not provide power during an outage.

## Native Windows preparation

1. Keep Ollama installed. The existing normal Ollama instance is left alone.
2. Run `Setup-Apex-Apocalypse.cmd` while online. It starts its own Ollama daemon
   on loopback port 11435, downloads `qwen3:4b` into
   `D:\Apex-Apocalypse\models`, verifies local weights and generates a test reply.
   This is approximately a 2.5 GB initial model download. At least 8 GB free
   disk is required as headroom. Large model choices may need much more.
3. Run `Start-Apex-Apocalypse.cmd`, then open
   `http://127.0.0.1:7862/apocalypse`. Use your existing Apex owner token.
4. Add local TXT, Markdown or PDF documents. Browser uploads are limited to
   8 MB; copy larger files into `D:\Apex-Apocalypse\documents`.

Uploads store local files; they are not automatically indexed into Apex's
knowledge base. Ask Apex to read them, or use NOMAD document RAG after its
AI service is prepared.

Set `APEX_APOCALYPSE_HOME` in the launching terminal to choose another folder.
`APEX_APOCALYPSE_MODEL` chooses another already-downloaded local model;
`Setup-Apex-Apocalypse.cmd --model MODEL` prepares it first. Cloud Ollama
aliases are rejected. No normal `.env`, system settings or registry settings
are changed. Running `Start-Apex-All.cmd` afterwards retains normal behavior.

Apocalypse uses Apex's existing memory/skills/tool engine and existing database.
The default launch initializes all database tables and attaches AgentCore to
the dashboard without starting autonomous monitors or MCP servers. Add
`--resident` to use the full resident boot path with the offline environment.
Some tools and spatial model loaders need downloaded assets or services;
the dedicated Apocalypse screen itself has no CDN dependencies.

## Project NOMAD integration

### English knowledge and worldwide map preparation

Run `Download-Apex-Apocalypse.cmd` while online. It prepares the native AI model,
then stages the comprehensive English collections and one complete English
Wikipedia edition with images, plus the latest worldwide detailed PMTiles map.
Alternative compact/no-image editions are not also downloaded. The current
selection is roughly 330 GiB; exact sizes and versions are resolved from Kiwix
and Protomaps metadata. Courses are separate Kolibri channel imports.

The queue works before Docker is installed. Files go directly into
`D:\Apex-Apocalypse\nomad\storage\zim` and `maps\pmtiles`; temporary `.part`
files stay beside them. Nothing large is staged on C:. A saved
`english-worldwide-plan.json` pins archive versions and records progress.
Rerun the command to resume interrupted downloads. Archives are marked
downloaded only after checking the publisher checksum. Existing completed
archives are rechecked and preserved if corrupt; failed partial files are also
kept for review. The entire remaining selection must fit with 20 GiB headroom.

`python scripts/prepare_apocalypse_library.py --plan-only` resolves metadata
without downloading archives. `--refresh-plan` explicitly selects newer versions
and keeps old files. The Apocalypse screen shows the last saved queue counts;
it does not assume the download process is still running or that readers work.
Missing catalog references stay visible instead of being claimed as installed.
The FDA drug dataset needs NOMAD's database ingestion pipeline, not a raw ZIM
download. Native staging does not register upstream collection-install records;
rescan the library to make saved ZIM files readable, and avoid reselecting the
same tiers in NOMAD to prevent duplicate download requests.

Once Docker is installed and its image storage is on a drive with room:

1. Run `Setup-Apex-Apocalypse.cmd --nomad` to build/start the pinned stack.
   First-time MySQL initialization can take several minutes on an external HDD.
   Its health check allows a 15-minute initialization grace period and checks
   TCP readiness, so the temporary initialization server cannot start NOMAD early.
   If an older setup reports MySQL unhealthy, inspect its logs and health first;
   once healthy, use `--nomad-start` to reuse built images without downloading.
2. Run `Setup-Apex-Apocalypse.cmd --nomad-apps` to request Kiwix, Kolibri Gen 2,
   CyberChef and FlatNotes, rescan saved books, and prepare map fonts/styles and
   the world overview. Check app health in NOMAD after installation.
3. In Kolibri, finish its first-run setup and import the English channels you
   want. Video courses are not included in the ZIM preparation queue.
4. Configure NOMAD AI/RAG and its embedding model separately. Its default
   Ollama host port 11434 is already used by native Ollama on this machine;
   do not install a competing default container or download through a daemon
   whose model-storage drive has not been checked.
5. Prepare the medical database through NOMAD and run a physical disconnect
   test for chat, books, a detailed map and courses.

NOMAD child apps require the upstream `project-nomad_default` network. The
generated Compose file names it explicitly. Child volume seeds use Linux
placeholder paths so upstream can resolve the actual Docker storage mount;
Windows drive-letter paths cannot be used in its Linux bind parser.

### Storage advice

A mechanical hard disk can hold models, books, maps and videos. Model loading,
file searches and random map/database reads can be slower. After a model fits
in RAM/VRAM, generation mainly depends on the processor/GPU and memory;
models that spill out of memory can suffer much more. Keep bulk archives on
D: now. Prefer SSD storage for frequently used models and Docker/database
workloads when space is available. A 1–2 TB SSD is a practical upgrade target,
but check the laptop's compatible slot/type before purchasing.

C: was down to about 1.8 GiB during this check. Free space before installing
Docker/WSL or expanding caches; moving only the library does not move Docker's
disk image or system temporary files. No files are deleted or disks relocated
by these scripts.

Official source: https://github.com/Crosstalk-Solutions/project-nomad

The complete 663-file source snapshot at
`5e1702efebfc5549f870911b2b042ed3bd312fcd` is preserved in
`integrations/project-nomad-source.zip`. `UPSTREAM.json` records its SHA-256.
The original Apache-2.0 license and README are included. The upstream source
archive is unmodified. Apex adds a separate UI, setup scripts and Compose
configuration. Libraries, courses, map datasets and optional apps have their
own licenses and are not included in the source archive.

NOMAD supplies:

- Ollama/OpenAI-compatible chat, uploaded-document RAG and Qdrant.
- Kiwix ZIM information library, Wikipedia selection and content explorer.
- Kolibri education and learning progress.
- ProtoMaps regional PMTiles maps and geographic extracts.
- CyberChef data tools and FlatNotes.
- Supply Depot curated/custom container apps, setup wizard, content management,
  system benchmark and optional update scheduling.

Those features remain in the original NOMAD interface. Apex's Apocalypse page
provides the local session and readiness entry. NOMAD Command Center readiness
does not mean every optional app, dataset or offline reader is installed.

### Windows Docker preparation

NOMAD is designed for Debian-based Linux. Its community Windows path uses
WSL2/Linux containers. Install Docker Desktop with Linux containers (or follow
the official WSL2 guide); configure its disk-image location on D: before
building. Docker images consume significant storage separately from library
files. Apex does not install Docker, change WSL distributions or relocate its
disk automatically.

Run `Setup-Apex-Apocalypse.cmd --nomad` while online. The helper:

- Validates the original archive and extracts it into the offline drive.
- Builds the verified pinned NOMAD source, rather than a drifting `latest` image.
- Uses a separate generated `nomad/Dockerfile.apex` to download the pinned
  go-pmtiles tool over HTTP/1.1 with resumable transfers. Docker's locked build
  cache retains partial downloads across failed attempts/builds; six explicit
  attempts resume from the saved byte offset. Each attempt allows 30 minutes,
  stopping if transfer speed stays below 1 KiB/s for two minutes. The old
  five-minute cutoff could interrupt a healthy slow connection (curl exit 28).
  This also avoids HTTP/2 stream failures (exit 92). Completed files must pass
  the pinned SHA-256 check before extraction. Original source and earlier build
  instructions remain unchanged, so completed layers can be reused. Do not
  prune Docker's build cache while resuming preparation; it holds these partials.
- Generates local app/database secrets in `D:\Apex-Apocalypse\nomad\.env`.
- Keeps storage, MySQL and Redis data under that same directory.
- Starts the Command Center only on `127.0.0.1:8080`. It needs access to
  Docker's socket to manage its apps, as in upstream NOMAD.

The adapter deliberately excludes the unauthenticated logs sidecar, automatic
core updater and host disk-collector. Core updates remain reviewed source
updates. Optional app/content updates are managed in NOMAD and require internet.
NOMAD-installed child apps use its own upstream network bindings; check those
before using a shared network. Do not expose its unauthenticated UI publicly.
The Docker deployment is not validated until Docker is available and the stack
has been built and run on the intended host.

The pinned source includes Creator Packs support, but the public source build
does not include the upstream private Creator Packs key. Licensed/paid packs
and hardware/account-dependent Supply Depot apps are not automatically
provisioned. The omitted updater, logs sidecar and host disk collector are
still adapter differences, not completed feature parity.

Open NOMAD, install the desired readers/apps, and download libraries, courses
and map regions. Later, `Setup-Apex-Apocalypse.cmd --nomad-start` starts cached
management images with `--pull never --no-build`. It does not fetch missing
images. Individual app/content preparation must already be complete.

## Offline boundary and verification

Apocalypse's child environment selects local models for foreground/background
reasoning, clears cloud API settings, disables cloud relay/subscription modes
and prevents Hugging Face downloads. Provider checks reject cloud model
switches and inspect Ollama model metadata for local weights. Its own Ollama
daemon starts with `OLLAMA_NO_CLOUD=1`. Python socket connections and DNS
lookups outside loopback are blocked. MCP discovery is paused because external
servers have their own networking. This is not an OS firewall for native
executables, Docker containers, browsers or other running Apex sessions.

Before depending on it, disconnect internet and test a new local chat response,
your saved documents, installed NOMAD readers, courses and map region. Readiness
reports do not claim a physical disconnect test was performed. Live God's Eye
feeds, remote basemaps, Composio/app APIs, online search and cloud AI require
internet; there is no silent cloud fallback.

## Celine

Qwen speech, the voice recording and speech-recognition weights must already
exist locally. The dedicated offline dashboard reports local voice health but
does not download or start GPU speech automatically. Use a cached local voice
server and refresh readiness, then test playback/microphone through Companion.
Text chat is independent of voice availability. Starting a voice process for
offline use should set `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`; missing
speech models then fail visibly rather than downloading during an outage.

## Source references

- NOMAD README and source snapshot in `integrations/project-nomad`.
- Ollama model: https://ollama.com/library/qwen3:4b
- Ollama local-only and storage settings: https://docs.ollama.com/faq


## Small library batches and Qwen transfer recovery

To prepare only whole reference archives below 1 decimal GB, run:

```cmd
.venv\Scripts\python.exe -X utf8 scripts\prepare_apocalypse_library.py --max-download-gb 1
```

The original full plan is retained. This selects whole archives by size; full
Wikipedia and worldwide maps do not fit and remain deferred. `--only ID` can be
repeated to choose named archives. The limit covers selected archive sizes, not
all network traffic across repeated attempts or the separate AI model download.
Archives are checksum verified; downloaded content still needs a prepared NOMAD
reader. Medical references are source material, not personalized medical advice.

If normal Ollama transfers repeatedly return EOF, the reviewed Qwen3:4b adapter
can use the Windows downloader:

```cmd
Setup-Apex-Apocalypse.cmd --model qwen3:4b --model-transport curl
```

This pins the official manifest inspected on 4 October 2026 (SHA-256
`359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7`) and
retains weights, template, parameters and license. The store layout was checked
against Ollama 0.35.1's `manifest` package. Upstream source:
https://github.com/ollama/ollama/blob/v0.35.1/manifest/manifest.go

Sequential `.apex-curl.part` files are separate from Ollama's preallocated
partials. They resume by byte offset and must pass the pinned SHA-256 before
installation. A different installed manifest or corrupt complete file stops
preparation for review. Existing files are retained. The setup still verifies
local model metadata and asks the GPU/CPU model for a response before writing
its prepared marker. The fallback is optional; normal Ollama remains the default.
A successful small metadata transfer does not establish full weight-transfer or
local-inference readiness.

## Local chat reply limits

The offline agent allows up to 8,192 generated tokens per request, including
Qwen's reasoning. Its private Ollama context remains 4,096 tokens to limit
memory use; the output allowance does not increase that context allocation.
Long generations can shift context, so split large documents or complex tasks
into smaller questions rather than relying on the entire history staying in view.

A reasoning-only or empty local response is reported as incomplete and is not
saved as an assistant answer. Existing empty replies are translated safely so
they cannot make the following request invalid. A partial final answer carries
a length-limit notice. Apex does not display hidden reasoning as a final answer,
automatically repeat tools, or switch to a cloud provider to recover.

The offline launcher reports the exception type, fixed category and HTTP status
when chat fails. Raw provider exceptions, prompts and tokens are excluded from
these error logs. Restart `Start-Apex-Apocalypse.cmd` after installing Python
changes, then refresh the page. Existing model weights and archives are reused.
