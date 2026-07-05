# Apex — Decision Record (2026-07-06)

Resolves the 5 open decisions from the 2026-07 audit / roadmap. Drop into `docs/`.

## (a) Sandbox default → sandbox autonomy, host for user

Origin-based, not config-based: anything self-initiated (cortex `run_python`, forged tools, skill runs) executes in `DockerBackend` unconditionally. Direct user-initiated commands may run on host, but route through `safety.check` (adds `run_python`/`run_skill` to the gate). If Docker is unavailable, autonomous execution degrades to staged-for-approval — never silent host execution.

## (b) Forged tools → approval for all

Every forged tool stages via `approvals.py` on first install; push notification fires. Hash-pin approved code — re-approval only on change. The model-declared `is_read_only` flag is no longer trusted for anything. Revisit verified auto-approve only if approval volume becomes a real cost.

## (c) Channels → unknown, therefore deny-by-default

Live-channel set is currently unknown. Policy that makes that safe: empty allowlist = **deny-all** (flip the current allow-all). A channel is live only when explicitly enabled with a configured webhook secret; signature verification required on every enabled inbound webhook (Telegram secret token, Twilio request signature, WhatsApp/Signal HMAC). Action: audit `.env` for live tokens; disable the rest.

## (d) Distribution → private until the learning loop ships

Roadmap item 6 deferred. Revisit public/OSS only after restraint (item 4) + reranker (item 5) exist — ship the differentiator before the audience. Security bar stays personal-scale meanwhile.

## (e) Learning loop → reranker now, fine-tune gated

Best-of-n reranked by logged 👍/👎 + `compare.py` preferences. Model-agnostic, reversible, and it accumulates the dataset a fine-tune would need. Fine-tune is gated on: measured lift in approval rate AND sufficient preference volume. Not before.

## Roadmap impact

Item 1 is now fully specified and unblocked: Docker-for-autonomy, safety-gate `run_python`/`run_skill`, distrust `is_read_only`, deny-all allowlists, webhook signatures, async-offload channel handlers. Item 6 drops off the active roadmap. Item 5's scope = reranker only.

**Open action:** `.env` channel-token audit (5 min, at the machine) to learn what (c)'s deny-all actually turns off.
