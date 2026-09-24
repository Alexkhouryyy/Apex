# Apex Relay

A mailbox for Apex. **Not a second Apex.**

Your laptop is Apex and stays the only thing that writes to memory. This holds
two things while the laptop is off, and hands them back when it returns:

- the last **snapshot** of memory, sealed, so your phone still has something to read
- an **outbox** of work that arrived, drained in order

It does not reason. It holds no model key. **It cannot open anything it stores.**

## Deploying it

**Step by step on a cloud server, with the phone page and the lid-shut test:
[`docs/RELAY_DEPLOY.md`](../docs/RELAY_DEPLOY.md).** What follows is the short
version and the reasoning.

One file, standard library only. No `pip install`.

```bash
scp relay/server.py you@your-box:~/apex-relay/
ssh you@your-box
cd apex-relay
export RELAY_SERVER_TOKEN="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"
python3 server.py
```

It refuses to start without a token, and serves nothing if one is somehow unset
later. That is deliberate: `if token and token != given` accepts everyone when
the token is empty, and that exact inversion already shipped once in Apex's own
dashboard middleware, where it hid a real authorisation bug for weeks. Here it
would publish your memory to the internet.

### TLS

`server.py` speaks plain HTTP and binds `127.0.0.1`. Put something in front:

```bash
# Simplest, if the box is on your Tailnet — free certs, no ports open
tailscale serve --bg 8799

# Or Caddy, two lines
apex-relay.example.com {
  reverse_proxy 127.0.0.1:8799
}
```

Binding wider is `RELAY_SERVER_HOST=0.0.0.0`, and the process says so on startup
when you do. Don't, unless something else is terminating TLS.

### Keeping it up

```ini
# /etc/systemd/system/apex-relay.service
[Unit]
Description=Apex Relay
After=network.target

[Service]
WorkingDirectory=/home/you/apex-relay
Environment=RELAY_SERVER_TOKEN=...
ExecStart=/usr/bin/python3 server.py
Restart=always

[Install]
WantedBy=multi-user.target
```

## Pointing Apex at it

On the laptop, in `.env` — via `scripts/set_env_key.py`, never a shell
redirect:

```
RELAY_ENABLED=true
RELAY_URL=https://apex-relay.example.com
RELAY_TOKEN=<the same RELAY_SERVER_TOKEN>
RELAY_KEY=<python -m agent.relay --new-key>
```

**`RELAY_KEY` never leaves the laptop.** It is not in this README's server
config and there is nowhere on the relay to put it. Keep a copy somewhere safe:
a snapshot sealed with a lost key is lost.

## Then check it, before trusting it

```
python -m agent.relay --check
```

This is the step that was missing. `python -m agent.relay` on its own reports
what the CURRENT PROCESS has done, so in a freshly opened shell it says
"never_pushed" whether the relay is perfect or unplugged — which is the wrong
instrument for the only question you have after setting one up.

`--check` talks to the real relay and reports each stage, exiting non-zero if
any fail. A healthy one looks like this:

```
Relay: everything checked passed.
  [ok  ] configured: pointing at https://apex-relay.example.com
  [ok  ] relay refuses strangers: unauthenticated request got 401
  [ok  ] the snapshot already there opens: 835684 bytes, and this key opens it
  [ok  ] snapshot uploads: 835684 bytes sealed and sent
  [ok  ] snapshot comes back: 835684 bytes, as stored
  [ok  ] relay cannot read it: stored as ciphertext (b'gAAAAABq'...)
  [ok  ] your key opens it: unsealed 626688 bytes; a real database
  [ok  ] context uploads: 636 chars from conversation, memories, schedule
```

Two of those stages are the security claim rather than a formality:

- **relay refuses strangers** fetches `/snapshot` with no token at all. If that
  succeeds, anyone who finds the URL has your sealed memory, and the check says
  so instead of moving on.
- **relay cannot read it** reads the bytes the relay actually stored and looks
  at them. It does not go through `pull_snapshot()`, which unseals on the way
  past and would return the comfortable answer every time.

**the snapshot already there opens** runs BEFORE the check pushes anything. A
laptop restored from a backup with a stale `RELAY_KEY` would otherwise be told
it was healthy — the push re-seals with the current key, and the stage then
compares that key against itself. Nothing recovers a snapshot whose key is
gone, so being told immediately matters.

When that stage fails, **nothing is pushed** — the check stops there, and so
does Apex's own relay loop, which would otherwise overwrite the snapshot within
seconds of boot. The stored copy stays readable by the original key until you
restore it. If that key is gone for good and you want to start over, set
`RELAY_OVERWRITE_UNREADABLE=true`.

## Configuration, in full

| Variable | Default | |
|---|---|---|
| `RELAY_SERVER_TOKEN` | *(none)* | Required. Without it nothing starts and nothing is served |
| `RELAY_SERVER_DB` | `relay.db` | |
| `RELAY_SERVER_HOST` | `127.0.0.1` | |
| `RELAY_SERVER_PORT` | `8799` | |
| `RELAY_SERVER_MAX_BYTES` | 64 MiB | Largest snapshot accepted |

## What it stores

```
snapshot   one row, enforced by CHECK (id = 1)   updated_at, byte_len, ciphertext
outbox     created_at, kind, ciphertext, done_at
questions  created_at, text, status, claimed_at, answered_at, reply_id, error
```

`questions`, like `replies` and `context`, is readable: the answerer has to
read a question to answer it. They come from your own phone, with your token.

Two BLOB columns and some timestamps. There is no column for anything readable,
which is the schema making the promise rather than this file doing it.

## Endpoints

| | | |
|---|---|---|
| `GET` | `/health` | No auth. Returns `{"ok":true}` and nothing else — anything more is a fact about you served to strangers |
| `GET` | `/phone`, `/` | No auth. The phone page — static, no data in it |
| `GET` | `/status` | How old the context and snapshot are, and when the answerer last asked for work |
| `POST` | `/questions` | Ask: `{"text": "..."}`, up to 2000 characters, at most 50 waiting |
| `GET` | `/questions` | The 20 most recent, with answers |
| `GET` | `/questions/{id}` | One question: `queued`, `answering`, `answered` or `failed` |
| `GET` | `/questions/pending` | For the answerer; also records that it is alive |
| `POST` | `/questions/{id}/claim` | For the answerer. `changed: 0` means someone else has it |
| `POST` | `/questions/{id}/error` | For the answerer: record why an answer failed |
| `PUT` | `/snapshot` | Store sealed bytes. Empty bodies refused |
| `GET` | `/snapshot` | Return them. `404` when none stored, never an empty `200` |
| `GET` | `/snapshot/meta` | Size and age, without the bytes |
| `POST` | `/outbox` | Add a sealed item (`X-Apex-Kind` optional) |
| `GET` | `/outbox` | Pending items, oldest first |
| `POST` | `/outbox/{id}/done` | Mark drained. Reports `changed: 0` if it already was |
| `PUT` | `/context` | The readable working slice the answerer reasons over |
| `GET` | `/context` | Read it back. `404` when none sent yet |
| `POST` | `/reply` | An answer from `answer.py`. Empty answers refused |
| `GET` | `/replies` | Replies the laptop has not filed yet |
| `POST` | `/replies/{id}/done` | Mark filed |

## Asking from your phone

Open `https://<your-relay>/phone` on your phone — the same address the laptop
uses as `RELAY_URL`, with `/phone` on the end. The first time, it asks for the
relay token (the `RELAY_TOKEN` from the laptop's `.env`) and keeps it on that
phone only; "Forget the token" removes it.

Ask a question and the page waits for the answerer (below) to pick it up. It
shows:

- **Answerer online / offline** — whether `answer.py --watch` has asked for
  work in the last 30 seconds. Offline means questions will wait, and after
  20 seconds each one says so instead of spinning.
- **Laptop last sent …** — how old the summary is that answers come from.
- Any action the answer needs, as **queued for your laptop**, never done.

The page itself holds no data and loads nothing from anywhere else; the one
inline script and style are pinned by hash in its Content-Security-Policy, and
every answer is shown as text, never as markup. On a phone, "Add to Home
Screen" makes it an app icon.

## Optional: letting it answer while the laptop is off

`server.py` alone is a mailbox. It holds no model key and does no reasoning, and
if you only run that, it never will.

`answer.py` is the half that answers. Separate file, separate process, separate
decision — answering is opt-in at the level of *which processes you start*, not
a flag inside one that a config you did not write could flip.

```bash
export ANTHROPIC_API_KEY=...      # ONLY this file needs one
export RELAY_SERVER_TOKEN=...     # the same token
python3 answer.py "what did I say about the Berlin trip?"
```

For the phone page, run it as a service instead, so it keeps answering:

```bash
python3 answer.py --watch
```

```ini
# /etc/systemd/system/apex-answer.service
[Unit]
Description=Apex Relay answerer
After=apex-relay.service

[Service]
WorkingDirectory=/home/you/apex-relay
EnvironmentFile=/etc/apex-relay.env     # RELAY_SERVER_TOKEN and ANTHROPIC_API_KEY
ExecStart=/usr/bin/python3 answer.py --watch
Restart=always

[Install]
WantedBy=multi-user.target
```

It claims each question before answering it, so two answerers never answer one;
a question claimed by an answerer that then died is offered again after three
minutes; and a failed answer is recorded against the question with its reason.

It reads the working context your laptop pushed, asks a model, and POSTs the
answer back. It touches no account, runs no command, opens no file. When
answering properly would need one of those, it records a **request** instead —
and your laptop turns that into a queued task that goes through `safety.check`,
`mcp_policy.enforce` and `subagent_scope.check` before anything happens.

**The cloud can want something to happen. It cannot be the thing that approves
it.**

### What the laptop does with a reply

A reply is **data, never an instruction.** It was written on this box, by a
model, from a context this box could read. Apex files the answer as a note and
turns `requests` into queued tasks — it never executes anything a reply
contains. So if this box is compromised, the worst a forged reply achieves is a
wrong answer and a task sitting visibly in your queue.

Replies are stored in plaintext, in their own table. Outbox items come from your
devices and are sealed with a key this box does not have; a reply is written
here by something that had to read the context to produce it, so sealing it
would be theatre. Two origins, two trust levels, two tables.

## If the box is compromised

Someone gets a sealed blob and a queue of sealed blobs. Without `RELAY_KEY`
they are noise, and the tag on each means a modified snapshot fails to open
rather than opening as something else.

They also get the ability to *withhold* — to serve your phone a stale snapshot,
or drop outbox items. That is a real cost and it is not fixed by encryption.
It is the reason this box is never allowed to be the record: everything it
holds is a copy of something the laptop already has, or work the laptop has not
yet seen.
