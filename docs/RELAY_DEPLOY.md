# Deploying the relay to a cloud server — from zero

This is the G2 gate in `docs/APEX_V2_PLAN.md`: **the laptop lid is shut, and
your phone still answers from Apex's memory.** About 30 minutes, once.

What runs where:

| Where | What | Holds |
|---|---|---|
| Your laptop | Apex, as always | Your memory, and `RELAY_KEY` — the only key that can open it |
| Cloud server | `relay/server.py` — the mailbox | Sealed snapshots it cannot open, a short summary, your questions |
| Cloud server | `relay/answer.py --watch` — the answerer | A model API key (DeepSeek or Anthropic). Nothing else |
| Your phone | `https://<relay>/phone` in the browser | The relay token |

The network is **Tailscale**: a free private network between your own
devices. No domain, no open ports, and nothing reachable from the internet.
Install Tailscale on the laptop and the phone too, signed in to the same
account.

For how any of this works, or what each stage of the check means, see
`relay/README.md`.

---

## Part A — the cloud server (Ubuntu), ~15 minutes

Any small Linux server works (a $5/month one is plenty). These commands are
for Ubuntu; replace `user@SERVER_IP` with yours.

**On the laptop** (Command Prompt, in the Apex folder). Make a token — a fresh
one, never one that has been pasted into a chat:

```cmd
.venv\Scripts\python -c "import secrets;print(secrets.token_urlsafe(32))"
```

That is **YOUR_TOKEN**. Copy the two relay files over:

```cmd
ssh user@SERVER_IP "mkdir -p ~/apex-relay"
scp relay\server.py relay\answer.py user@SERVER_IP:~/apex-relay/
```

**On the server** (`ssh user@SERVER_IP`):

```bash
sudo apt update && sudo apt install -y python3 curl
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up

# The token, readable only by root.
echo "RELAY_SERVER_TOKEN=YOUR_TOKEN" | sudo tee /etc/apex-relay.env >/dev/null
sudo chmod 600 /etc/apex-relay.env

# The mailbox, kept running and restarted on crash and reboot.
sudo tee /etc/systemd/system/apex-relay.service >/dev/null <<EOF
[Unit]
Description=Apex Relay
After=network.target
[Service]
User=$USER
WorkingDirectory=/home/$USER/apex-relay
EnvironmentFile=/etc/apex-relay.env
ExecStart=/usr/bin/python3 server.py
Restart=always
[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload && sudo systemctl enable --now apex-relay

curl -s http://127.0.0.1:8799/health        # must print {"ok": true}
sudo tailscale serve --bg 8799
tailscale serve status                       # note the https://….ts.net address
```

That `https://….ts.net` address is **RELAY_URL**.

## Part B — point Apex at it (laptop)

```cmd
.venv\Scripts\python -m agent.relay --new-key
```

That prints **RELAY_KEY**. **Save a copy somewhere safe — a password manager.
If this key is lost, everything the relay holds can never be opened again.**
It never goes on the server.

```cmd
.venv\Scripts\python scripts\set_env_key.py RELAY_ENABLED true
.venv\Scripts\python scripts\set_env_key.py RELAY_URL https://YOUR-NAME.ts.net
.venv\Scripts\python scripts\set_env_key.py RELAY_TOKEN YOUR_TOKEN
.venv\Scripts\python scripts\set_env_key.py RELAY_KEY YOUR_KEY
.venv\Scripts\python -m agent.relay --check
```

Every line should say `[ok  ]`. A `FAIL` line says what to fix on the line
under it. Then restart Apex (`Apex.bat`) so it starts sending snapshots and
the summary on its own.

## Part C — the answerer, and the phone page

**On the server**, give the answerer a model key — the same DeepSeek key Apex
uses is fine — and run it as a service:

```bash
echo "DEEPSEEK_API_KEY=YOUR_DEEPSEEK_KEY" | sudo tee -a /etc/apex-relay.env >/dev/null
# or, for Claude instead:  ANTHROPIC_API_KEY=YOUR_ANTHROPIC_KEY

sudo tee /etc/systemd/system/apex-answer.service >/dev/null <<EOF
[Unit]
Description=Apex Relay answerer
After=apex-relay.service
[Service]
User=$USER
WorkingDirectory=/home/$USER/apex-relay
EnvironmentFile=/etc/apex-relay.env
ExecStart=/usr/bin/python3 answer.py --watch
Restart=always
[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload && sudo systemctl enable --now apex-answer
sudo systemctl status apex-answer --no-pager   # should say "watching … for questions"
```

**On the phone**, with Tailscale on, open:

```
https://YOUR-NAME.ts.net/phone
```

Paste YOUR_TOKEN once. The page should show **Answerer online** and **Laptop
last sent …**. On iPhone or Android, "Add to Home Screen" makes it an icon.

## Part D — the G2 test

1. With Apex running on the laptop, tell it something specific ("my dentist
   is Tuesday at 3pm"), and wait a minute for the summary to go up.
2. **Shut the lid.**
3. On the phone, on mobile data rather than home Wi-Fi if you can, open
   `/phone` and ask about it.
4. **It answers from what you told Apex → G2 passes.**
5. Open the lid. The laptop files the answer into memory on its own.

## When something is wrong

| You see | It means | Do this |
|---|---|---|
| `--check`: *relay refuses strangers* FAIL | The relay served data with no token | Stop it: `sudo systemctl stop apex-relay`, check `/etc/apex-relay.env`, start again |
| `--check`: *the snapshot already there opens* FAIL | This laptop's `RELAY_KEY` is not the one that sealed what is stored | Restore the saved key. Nothing was overwritten |
| Page: **Answerer offline** | `answer.py --watch` is not running | `sudo systemctl status apex-answer`, then `journalctl -u apex-answer -n 50` |
| Page: **Laptop last sent never** | Apex has not pushed a summary | Is `RELAY_ENABLED=true` on the laptop, and was Apex restarted after? |
| Page: a question **Failed: …** | The answerer tried and the model call failed | The reason is on the card — usually the API key. "no model key" means neither `DEEPSEEK_API_KEY` nor `ANTHROPIC_API_KEY` is in `/etc/apex-relay.env` |
| Page asks for the token again | The token was refused | It must equal `RELAY_TOKEN` in the laptop's `.env` exactly |

## Updating later

After `git pull` on the laptop, copy the two files again and restart both:

```cmd
scp relay\server.py relay\answer.py user@SERVER_IP:~/apex-relay/
ssh user@SERVER_IP "sudo systemctl restart apex-relay apex-answer"
```

The relay's database carries over; new columns are added on start.

## What this does not do yet

- Answers come from the **summary** the laptop pushes (recent conversation,
  memories, schedule) — not the full memory, which stays sealed and unread.
- A question asked from the phone is answered in the cloud; the laptop only
  files the answer when it wakes.
- With both keys on the server, Anthropic answers; set
  `RELAY_ANSWER_PROVIDER=deepseek` in `/etc/apex-relay.env` to pin DeepSeek.
  `RELAY_ANSWER_MODEL` overrides the model (defaults: `deepseek-flash`,
  `claude-haiku-4-5-20251001`).
