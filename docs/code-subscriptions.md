# Coding subscriptions, images and memory

Apex Code uses the official signed-in `codex` and `claude` clients. ChatGPT
subscription access and API billing are separate. Apex does not copy OAuth
credentials or send subscription tokens to an unofficial backend.

## Images

Ask ChatGPT in Apex Code to generate an image. On a Codex version and account
with built-in image generation, Apex asks the client to save raster artifacts
under `generated_images/` in the session worktree. Apex displays new or changed
PNG, JPEG and WebP files in the feed and file viewer, including ignored files.
The viewer verifies their format, limits previews to 10 MB, and rejects paths
that resolve outside the project. Generated files are subject to the project's
normal Git ignore and Keep behavior; ignored artifacts are not merged into the
main checkout automatically.

The general Apex `generate_image` tool accepts `provider="chatgpt"` and uses the
same official Codex client. Failure, missing output or subscription limits return
an error rather than falling back to a billed API. `provider="replicate"` retains
the existing integration. `IMAGE_GEN_PROVIDER=auto` preserves Replicate when its
token is configured and otherwise selects ChatGPT. Set
`IMAGE_GEN_PROVIDER=chatgpt` to make the subscription your preferred image path.
The `model` parameter for ChatGPT selects the coding model; the native client
selects its image model. The requested size is a prompt instruction, not a
guarantee of an exact output resolution.

If no image appears, update the Codex client and confirm that the signed-in
ChatGPT account supports image generation and has available usage. Apex cannot
add capabilities or models that a subscription does not expose.

## Model selection

The model picker obtains Codex models through the official app-server
`initialize` / `model/list` protocol and Claude models through its SDK-compatible
streaming initialization protocol. Discovery does not submit a user prompt or
request inference. Results are cached for five minutes. If a CLI cannot expose
its catalog, Apex keeps an exact model ID entry and Claude's standard aliases.
An account default option leaves model selection to the provider.

Model and effort preferences are saved independently for ChatGPT and Claude.
Changing providers without specifying new options clears the other provider's
model and effort on the backend. The UI restores that provider's own saved
preferences. Codex catalog effort options are shown when supplied; custom model
IDs remain available when the catalog is incomplete. Legacy Codex `max` retains
its prior `high` mapping; select `xhigh` explicitly when the account model
supports it.

## Usage and run controls

Open **Usage** in the Code header, or select a session's token count. Apex shows
provider-reported input, output, cache reads/writes and reasoning counters when
available. Session totals include coding turns and second-opinion reviews, with
separate subtotals. A breakdown appears under each completed coding turn.
Missing counters stay **Not reported**; incomplete totals are labeled **partial**.
Historical scalar totals do not supply a detailed breakdown. Reopening a session
does not add its usage a second time.

The account section reads Codex's public `account/rateLimits/read` and
`account/usage/read` app-server methods without submitting a prompt. It shows
reported quota windows, remaining percentages, reset times and token activity.
Snapshots are cached for 60 seconds; **Refresh** bypasses that cache. Unsupported
methods or unavailable clients produce an explicit unavailable state. Claude's
read-only CLI integration supplies turn usage but no supported account-quota
protocol. Session tokens cannot determine remaining subscription quota.

Requested model/effort and the model actually reported by the client are labeled
separately. Turn token counts accumulate across model requests and cannot measure
current context utilization. Provider cost estimates, when present, are labeled
as estimates and are not subscription charges.

While a turn runs, the composer preserves your next-message draft. Use the
dedicated **Stop** button to interrupt. Sending while busy does not cancel the
turn. This exec-based integration does not yet queue or steer follow-ups.

See [the comparison record](code-comparison.md) for validation and live-test limits.

## Memory during a conversation

Before every message, Apex recomputes the current coding brief. If its knowledge
changes, it sends an updated snapshot into an existing provider conversation.
Approved coding preferences, project rules and forgotten memories therefore
take effect on the next turn without starting a new session. Unchanged knowledge
does not resend a snapshot just because session statistics changed. Previously
sent text remains in the provider's history; the refreshed snapshot instructs
the agent to recheck missing items before reusing them.

Default long-term text recall now uses the same bounded term search fallback as
the coding MCP server, so a question need not contain a memory's exact sentence.
This does not migrate or merge the separate experimental governed-memory store,
and does not promote unapproved memories into the coding brief.

## Upstream interfaces

- [Codex app-server model discovery](https://developers.openai.com/codex/app-server)
- [Codex image generation](https://developers.openai.com/codex/image-generation)
- [Claude Agent SDK Python initialization](https://code.claude.com/docs/en/agent-sdk/python)
- [Claude model configuration](https://code.claude.com/docs/en/model-config)
- [Hermes image generation implementation](https://github.com/NousResearch/hermes-agent/blob/main/tools/image_gen_tool.py)

Regression tests use real temporary Git worktrees and fake CLI protocols. The
owner's actual signed-in accounts require a subsequent PC smoke check for
subscription-specific model availability and image generation.
