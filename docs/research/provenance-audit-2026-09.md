# Apex provenance and licensing audit

Date: 2026-09-29. Baseline: `63dbec654e97470504342f21578a9c53e1aa47d3`.

## Decision

Keep the current root MIT license while resolving the findings below. The available evidence does **not** support describing all of Apex, its assets, its dependencies, and its voices as exclusively owned or uniformly MIT. A future proprietary release needs a defined distribution package and a review of its actual contents. This is a technical evidence audit, not a legal clearance opinion.

The audit produced a tracked-file inventory, dependency metadata inventories, source comparisons, and an actionable release checklist. It did not change repository visibility or application behavior. The narrow remediation is preserving the verified upstream license and provenance alongside the copied Composio instructions.

## Scope and method

- 644 tracked files hashed at the baseline; selected source/history reviewed, not every line manually audited.
- 44 desktop and 27 cloud requirement entries inspected. These overlap; they are not 71 distinct dependencies.
- 168 distributions inventoried in Apex's `.venv`; 93 in the fast voice environment and 90 in the older Qwen environment. Counts overlap and describe local installations, not shipping components.
- 210 trailer npm lockfile package entries, including platform-specific optional packages; 28 tracked image/audio/GLB assets inventoried.
- Exact upstream comparisons for vendored Three.js, Composio instructions, and all ten planet images. Existing imported-skill hashes and the OpenMotor STEP checksum checked.
- No application or downloaded upstream code executed. Package archives were read as data; installed packages were inspected through metadata. Private credentials, memory contents, and the Celine recording were not read.

Evidence: `provenance-audit-evidence-2026-09.json` records the compact, portable results. The accompanying local audit bundle retains full `inventory.json`, `upstream-verification.json`, `additional-evidence.json`, upstream license texts and the inspection scripts. Those inventories are not a complete release SBOM.

## Findings and actions

| Priority | Component | Evidence and required action |
|---|---|---|
| Before binary/container release | Distribution boundary | `Dockerfile` uses `COPY . .`; no `.dockerignore` exists. A build from this working directory can include local environments, settings, development materials and unrelated licensed components. Add explicit exclusions or an allowlisted release context and inspect the resulting image. No image was built or published during this audit. |
| Before redistribution of these images | Planet textures | All ten JPEG Git blobs exactly match `jeromeetienne/threex.planets` at `3de707594b1178ba32c62199bf29efdf90f59cf0`. Apex added them in `d010338`. Upstream credits Planet Pixel Emporium for image data; Apex has no adjacent attribution/license. The creator's website was unavailable during verification. Obtain the image-specific terms and notices, or replace with assets whose redistribution rights are documented. The library's MIT statement alone does not settle the underlying image rights. |
| Before proprietary desktop packaging | MouseInfo | PyAutoGUI 0.9.54 requires MouseInfo. Installed MouseInfo 0.1.3 and its PyPI source `setup.py` declare GPLv3+; the source archive contains no license file. Treat this as an unresolved copyleft packaging issue: verify exact upstream terms and integration, replace the dependency if needed, or meet applicable obligations. Do not silently relabel it BSD because PyAutoGUI itself is BSD. |
| Before binary packaging | LGPL and native dependencies | `pynput` 1.8.2 and `pystray` 0.19.5 include LGPLv3 terms; fast voice `soxr` 1.1.0 declares LGPL-2.1-or-later. Preserve notices and review the actual bundled libraries, source availability and replacement/relinking requirements. Inspect FFmpeg/PyAV, libsndfile, Chromium, CUDA and other native components in the final artifact. Python wrapper metadata does not clear bundled binaries. |
| Corrected by this audit | Composio skill | Both copies of all four instruction files match upstream `ComposioHQ/composio` at `b2f5098b28e9bfe1f0c2255b94adea7c5c2fa022` after line-ending normalization. Original `skills-lock.json` supplied a hash but no Git revision or license copy. Added the unmodified MIT notice (Sampark Inc., 2025) and a provenance record to each copy. This is a verified matching revision, not a claim about the installer's original checkout. |
| Before commercial trailer production | Remotion | `trailer/package.json` pins 4.0.290. Its license is not MIT: its free tier covers individuals, qualifying nonprofits and for-profit organizations with up to three employees; other cases need the company terms. Confirm the entity's eligibility. `private: true` only prevents npm publishing. The seven optional compositor entries with no license field require artifact-level review if shipped. |
| Before distributing Celine or avatars | Voice/image permissions | The Qwen Base model's Apache-2.0 declaration does not establish permission for the reference recording. Owner confirmation is pending. Two Ready Player Me CDN avatar URLs also have no per-avatar rights record in Apex. Verify authorization or use an owned replacement before distributing those assets or relying on them in a product. |
| Before a reproducible release | Dependency/model pins | Python requirements are mostly unpinned. Current model names/download URLs do not form a hash-verified model manifest. Produce separate desktop, cloud and voice lockfiles and license inventories from clean release builds; record each model revision, file hash, license and source. |
| Before an ownership/relicensing claim | Contribution history | The root MIT file was added in `88f9f20` on 2026-06-15 under the Git author label Claude, with Alex Khoury's copyright notice. That identifies the commit, not the rationale, the user's informed selection, or legal ownership of every contribution. Review contribution provenance and retain applicable third-party terms before choosing future-release terms. |

## Provenance that is already documented

**Three.js:** all 14 vendored JavaScript files plus `LICENSE` match the official `three@0.160.0` npm tarball after CRLF-to-LF normalization. Raw byte hashes differ only because of Windows line endings. Existing upstream MIT notice is preserved. Keep it with distributions.

**Hermes and OpenClaw skills:** both imported skills include repository, pinned revision, review notes and upstream MIT notice. All four stored file-hash checks pass. These are two imported skills, not evidence that the full Hermes or OpenClaw applications have been absorbed or cleared for reuse.

**OpenMotor:** compressed original STEP, derived viewer asset and assembly manifest have a separate CERN-OHL-W-2.0 provenance record, license and conversion script. The decompressed STEP SHA-256 matches the recorded value `0f6737f1ddba820376e88298cf05725de36048f03c227714bf391e7cf21b07d3`. Preserve this source, license and modification information with covered distributions; Apex's root MIT file does not replace them.

**Procedural assets:** the study models, refined chevron icons, banner and trailer score have in-repository generation scripts. The music script and MP3 were introduced together in `225c624`. This supports project provenance but is not independent proof of every output's authorship or an exact regeneration check. Screenshots, legacy assets and `clone-app` instructions still lack a comprehensive source/rights manifest.

**Markdown renderer:** `dashboard/static/marked.min.js` is a short project renderer introduced in `42b5dd6`, not established as the upstream Marked package merely by its filename. Do not manufacture a Marked attribution without source evidence.

## Hand tracking and upstream study limits

`agent/handtrack.py` expressly acknowledges reading Barehands before implementing its pinch calculation. It also asserts no copied code; this audit does not turn that assertion into a legal conclusion. A heuristic comparison of Barehands `server.py` and `stage.html` at `eb23bed2d772f9d5a24de26fb92f46c3c76d69cf` with Apex's handtrack, gestures, board backend and board frontend found no matching blocks of three or more consecutive normalized long lines. The screen cannot detect translated or substantially modified copying and is not a clean-room certification. Review this history specifically before proprietary licensing.

The prior 18-project study and its external research clones are research materials, not proof of imported code or a blanket license grant. Future imports need file-level provenance, an exact revision, original notices and a compatibility decision.

## Models and services

The inspected Qwen3-TTS Base and all-MiniLM-L6-v2 model cards declare Apache-2.0; SYSTRAN's faster-whisper-base card declares MIT. Apex's default speech model is `base`. The fast voice environment identifies `faster-qwen3-tts` 0.4.0 as MIT and `qwen-tts-hf` 0.1.1.post1 as Apache-2.0. Preserve artifact-specific notices and record revisions rather than assuming names remain unchanged. The MediaPipe task URL is recorded; model-bundle terms need to be captured separately from library and documentation terms.

Composio/OAuth app catalogs, model-provider accounts and MCP-connected services are separate service authorizations. An SDK's license neither grants access to accounts nor permits copying those services. No account settings were changed.

## Next sequence

1. Define the first distributable: Windows source install, packaged desktop app or cloud container. Build a clean, explicit file list for it.
2. Fix Docker exclusions; resolve/replace the planet images, avatar assets and MouseInfo dependency as appropriate for that distribution. Verify Celine's reference rights.
3. Generate dependency/model locks and a complete third-party notice bundle from the actual release artifact, including native libraries. Avoid treating environment metadata as the final legal classification: PyMsgBox's GPL classifier conflicts with its included BSD-style license; qrcode's generic proprietary classifier also needs context.
4. Review ownership and license compatibility with qualified counsel before a proprietary release. Then select terms for future contributions/releases and clearly document third-party exceptions. Changing a root file or repository visibility does not erase previously granted MIT permissions.

## Primary sources

- [MIT license](https://opensource.org/license/mit) and [GitHub's licensing guidance](https://opensource.guide/legal/).
- [Composio license at verified revision](https://github.com/ComposioHQ/composio/blob/b2f5098b28e9bfe1f0c2255b94adea7c5c2fa022/LICENSE).
- [Planet library's source attribution](https://github.com/jeromeetienne/threex.planets/blob/3de707594b1178ba32c62199bf29efdf90f59cf0/README.md). [Solar System Scope](https://www.solarsystemscope.com/textures/) offers documented CC-BY-4.0 alternatives for several bodies; this is a replacement option, not the source of Apex's current images.
- [Remotion 4.0.290 license](https://github.com/remotion-dev/remotion/blob/v4.0.290/LICENSE.md).
- [OpenMotor pinned license](https://github.com/eMotres/OpenMotor-Hardware/blob/1e1e56d7cf64ea393793ca5c06189251f87b6e98/LICENSE.txt).
- [Qwen model card](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base), [embedding model card](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2), [Whisper conversion card](https://huggingface.co/Systran/faster-whisper-base), [MediaPipe hand model documentation](https://developers.google.com/edge/mediapipe/solutions/vision/hand_landmarker).

Open questions are documented findings, not verified compliance. Runtime regression tests were not rerun for this documentation/notice-only change; evidence hashes and file boundaries were checked instead.
