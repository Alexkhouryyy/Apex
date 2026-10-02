# Call Apex: talk to it from the car

Your car already has a voice interface: hands-free Bluetooth calling. On a
2011 Grand Cherokee that's Uconnect, with a microphone in the headliner, the
car speakers, and a phone button on the steering wheel. Call Apex works
through it:

1. Press the steering-wheel phone button and say **"Call Apex"** (a contact on your phone).
2. Apex answers: *"Apex here. What do you need?"*. Talk normally; you can talk over it.
3. It says *"One moment"* while it thinks, then speaks a short answer, and
   asks *"Anything else?"*. Say **"bye"** or **"that's all"** to hang up.

| Your PC | Who answers | What it can do |
| --- | --- | --- |
| On, running Apex | Your full Apex (memory, tools, everything) | Anything Apex can. Before anything that changes something (sending, booking, deleting) it says what it will do and waits for your "yes" |
| Off or asleep | The cloud relay, from the summary your PC last sent | Answers questions. Anything needing the PC is queued for it ("I've queued that for your computer"), never done |

Answers are made for driving:
- **Short and spoken:** one to three sentences, with no lists or links.
- **Long tasks don't hold the line:** if Apex is still working after about 40
  seconds, it says so and texts you the answer when it's ready (PC on).
- **The voice is Twilio's** (`Polly.Joanna-Neural` by default; set
  `TWILIO_VOICE` to change it), not Celine's. Celine's voice runs on your PC's
  GPU, which a phone call can't reach. For Celine in the car, use the car page
  (`/drive`, [CAR_AND_SPATIAL.md](CAR_AND_SPATIAL.md)) on a mounted phone.

## How it's wired (and why your PC is never public)

```
your phone ──call──▶ Twilio number ──https──▶ cloud relay (public, /twilio only answers signed Twilio requests)
                                                  │
                                    PC answers? ──┤── yes: forwarded privately over Tailscale to Apex on your PC
                                                  └── no:  answered by the relay from your PC's last summary
```

- **Twilio calls one address: your relay.** It's the only public piece.
  - Every call request carries Twilio's signature, checked against the
    relay's address, and must come from a number on your list. Anything else
    is refused before anything happens.
  - With no Twilio secret, caller list or public address set, the relay
    refuses all calls.
- **Your PC isn't exposed to the internet.** The relay passes calls to it over
  your private Tailscale network (the same address the car page uses). The
  PC checks Twilio's signature too.
- **If the PC stops answering mid-call,** the relay takes over: *"Your computer
  stopped answering, so I'm answering from the cloud now."*

## Before you start: the cost

You pay for the Twilio number, plus a small per-minute charge for each call
and for speech recognition. **Check Twilio's current pricing for your
country** before buying. If Twilio can't give you a local number in your
country, a call to a foreign number (for example a US one) is an
**international call on your mobile plan**. Check that cost with your carrier
too.

## Set it up

You need:
- the cloud relay running ([RELAY_DEPLOY.md](RELAY_DEPLOY.md)), with
  `answer.py --watch` for PC-off answers;
- the car page set up on your PC (`Setup-Apex-Car.cmd`), which gives the PC
  its private Tailscale address.

### 1. Twilio

1. Make a Twilio account and buy a number with **Voice** (and **SMS**, if you
   want long answers texted to you).
2. Note your **Account SID** and **Auth Token** (Console → Account info).

### 2. The relay (cloud server)

Add these to `/etc/apex-relay.env`:

```
RELAY_TWILIO_AUTH_TOKEN=<Twilio Auth Token>
RELAY_CALLERS=+<your mobile number>        # comma-separate more than one
RELAY_PUBLIC_URL=https://<relay-name>.<tailnet>.ts.net
RELAY_PC_URL=https://<your-pc>.<tailnet>.ts.net
```

Then make the relay reachable by Twilio, and restart it:

```bash
sudo tailscale funnel --bg 8799      # public HTTPS; replaces the private `tailscale serve` for the relay
sudo systemctl restart apex-relay
```

Every relay route except `/health`, the static `/phone` page and the signed
`/twilio` routes needs your relay token. That's what makes it safe to be
public. Use a long random token (the deploy guide shows how).

### 3. Your PC (`.env` in the Apex folder)

```
TWILIO_SID=<Account SID>
TWILIO_AUTH_TOKEN=<Twilio Auth Token>
TWILIO_FROM_NUMBER=+<the Twilio number>     # used to text you long answers
PHONE_ALLOWED_NUMBERS=+<your mobile number>
TWILIO_PUBLIC_BASE_URL=https://<relay-name>.<tailnet>.ts.net
```

Restart Apex. Keep the car page's private address on (`Setup-Apex-Car.cmd`),
because that's how the relay reaches the PC.

### 4. Point the number at the relay

Twilio Console → Phone Numbers → your number → **Voice configuration** →
"A call comes in": **Webhook**, `https://<relay-name>.<tailnet>.ts.net/twilio/voice`,
**HTTP POST**. Save.

### 5. The car

1. Save the Twilio number on your phone as a contact called **Apex**.
2. Pair the phone with Uconnect if it isn't already.
3. Press the phone button and say "Call Apex", or dial it from the phone.

## When something's wrong

| You hear | Why |
| --- | --- |
| "This number is not authorized" | Your number isn't in `RELAY_CALLERS` / `PHONE_ALLOWED_NUMBERS`. Write it exactly as Twilio sends it, with `+` and the country code |
| "Your computer is off…" while the PC is on | The relay can't reach the PC: check Tailscale on the PC, `Setup-Apex-Car.cmd`, and `RELAY_PC_URL`. Or the PC refused the signature: check `TWILIO_PUBLIC_BASE_URL` is the relay's address, exactly |
| "The cloud answerer isn't running" | `answer.py --watch` isn't running on the relay server |
| Twilio's "application error" | The relay isn't reachable publicly (`tailscale funnel`), or `RELAY_PUBLIC_URL` doesn't match the webhook address |
