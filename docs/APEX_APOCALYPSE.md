# Apex Apocalypse

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
- Builds the original NOMAD Dockerfile, rather than a drifting `latest` image.
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
