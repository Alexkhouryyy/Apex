# The relay on Railway

The relay ([RELAY_DEPLOY.md](RELAY_DEPLOY.md)) can run as a second service
in your existing Railway project, next to the Railway Apex. It's one small
container holding three things:

- **The mailbox** (`relay/server.py`): the public HTTPS address for your
  laptop, your phone page and Call Apex.
- **The answerer** (`relay/answer.py`): it answers while the laptop is off,
  and is restarted automatically if it stops.
- **Tailscale** (optional): with it, the relay joins your private network, so
  Call Apex can hand calls to the Apex on your PC while it's on. Your PC stays
  private either way.

| Piece | Where |
| --- | --- |
| `relay/Dockerfile` | Python (standard library only) plus Tailscale's own binaries |
| `relay/start.sh` | Joins the tailnet if `TS_AUTHKEY` is set, starts the answerer, runs the mailbox |
| `relay/railway.toml` | Builds that Dockerfile; health check `/health` |

## 1. Add the service (Railway, in the browser)

1. Open your Railway project → **+ Create** → **GitHub Repo** → pick the Apex
   repository. That makes a second service. Rename it **apex-relay**.
2. In the new service:
   - **Settings → Config-as-code → Railway config file:** `/relay/railway.toml`.
     Without this it would build the full Apex again from the root `railway.toml`.
   - **Settings → Networking → Generate Domain.** Copy the address, for
     example `https://apex-relay-production.up.railway.app`. This is your
     **RELAY_URL**.
   - **+ Volume** (right-click the service, or Command palette → "Add volume"),
     mount path **`/data`**. Questions and the sealed snapshot live there and
     survive redeploys.

## 2. Make a token (on your PC)

```cmd
cd /d C:\Users\alexk\Apex
.venv\Scripts\python -c "import secrets;print(secrets.token_urlsafe(32))"
```

That's **YOUR_TOKEN**. Make a fresh one, and never one that has been pasted
into a chat.

## 3. Tailscale auth key (for handing calls to your PC)

Tailscale admin console → **Settings → Keys → Generate auth key**:
- tick **Reusable** and **Ephemeral**;
- copy the `tskey-auth-…` value.

Ephemeral means each redeploy joins fresh and old copies clean themselves up.

## 4. Variables (apex-relay service → Variables)

| Variable | Value |
| --- | --- |
| `RELAY_SERVER_TOKEN` | YOUR_TOKEN |
| `DEEPSEEK_API_KEY` | your DeepSeek key (or `ANTHROPIC_API_KEY` for Claude). This lets it answer while the PC is off |
| `TS_AUTHKEY` | the `tskey-auth-…` key |

Save. Railway redeploys. Its **Deploy logs** should show:

```
[relay] joined the tailnet as apex-relay
[relay] listening on 0.0.0.0:…
[answer] watching http://127.0.0.1:… for questions every 2s
```

Then open `https://<your-relay>.up.railway.app/health` in a browser. It must
say `{"ok": true}`.

## 5. Point Apex at it (your PC)

Same as [RELAY_DEPLOY.md](RELAY_DEPLOY.md), part B, with your Railway address
as `RELAY_URL`:

```cmd
.venv\Scripts\python -m agent.relay --new-key
```

Save the printed **RELAY_KEY** in a password manager. If it's lost, what the
relay holds can never be opened again. It never goes on Railway. Then:

```cmd
.venv\Scripts\python scripts\set_env_key.py RELAY_ENABLED true
.venv\Scripts\python scripts\set_env_key.py RELAY_URL https://<your-relay>.up.railway.app
.venv\Scripts\python scripts\set_env_key.py RELAY_TOKEN YOUR_TOKEN
.venv\Scripts\python scripts\set_env_key.py RELAY_KEY YOUR_KEY
.venv\Scripts\python -m agent.relay --check
```

Every line should say `[ok  ]`. Restart Apex.

## 6. The phone page

Open `https://<your-relay>.up.railway.app/phone`, paste YOUR_TOKEN once, and
ask something. It should show **Answerer online** and **Laptop last sent …**.

## 7. Call Apex

Follow [CALL_APEX.md](CALL_APEX.md). On Railway, the relay already has a
public HTTPS address, so skip `tailscale funnel` there. Use:

| Where | Variable | Value |
| --- | --- | --- |
| Railway (apex-relay) | `RELAY_PUBLIC_URL` | `https://<your-relay>.up.railway.app` |
| Railway (apex-relay) | `RELAY_PC_URL` | your PC's private address from `Setup-Apex-Car.cmd`, e.g. `https://alex-pc.tail1234.ts.net` |
| Railway (apex-relay) | `RELAY_TWILIO_AUTH_TOKEN`, `RELAY_CALLERS` | as in CALL_APEX.md |
| Your PC `.env` | `TWILIO_PUBLIC_BASE_URL` | `https://<your-relay>.up.railway.app` |
| Twilio number | Voice webhook | `https://<your-relay>.up.railway.app/twilio/voice` (POST) |

## Is the relay being public on Railway safe?

The relay was designed to sit behind a public address:
- **Token-gated:** every route needs YOUR_TOKEN, except `/health` (it says only
  `{"ok": true}`), the static `/phone` page, and the Twilio routes.
- **Twilio routes:** they need Twilio's signature *and* one of your numbers.
- **Closed when unconfigured:** with no token set it serves nothing.
- **Your memory stays sealed:** it's stored with a key that never leaves your PC.

**Your PC is never public.** The relay reaches it only through your private
Tailscale network.

## When something's wrong

| You see | Do this |
| --- | --- |
| Build used the full Apex Dockerfile | Set the config file path to `/relay/railway.toml` (step 1) |
| Logs stop at `tailscale up` or show an auth error | The `TS_AUTHKEY` is wrong, used, or expired. Make a new reusable key |
| `/health` fails, or the deploy keeps restarting | `RELAY_SERVER_TOKEN` missing: the relay refuses to start without it, and the logs say so |
| Phone page: **Answerer offline** | No `DEEPSEEK_API_KEY` / `ANTHROPIC_API_KEY`, or the answerer is crashing. Check the deploy logs |
| Questions vanish after a redeploy | No volume at `/data` (step 1) |
