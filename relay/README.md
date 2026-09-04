# Apex Relay

A mailbox for Apex. **Not a second Apex.**

Your laptop is Apex and stays the only thing that writes to memory. This holds
two things while the laptop is off, and hands them back when it returns:

- the last **snapshot** of memory, sealed, so your phone still has something to read
- an **outbox** of work that arrived, drained in order

It does not reason. It holds no model key. **It cannot open anything it stores.**

## Deploying it

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
```

Two BLOB columns and some timestamps. There is no column for anything readable,
which is the schema making the promise rather than this file doing it.

## Endpoints

| | | |
|---|---|---|
| `GET` | `/health` | No auth. Returns `{"ok":true}` and nothing else — anything more is a fact about you served to strangers |
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
