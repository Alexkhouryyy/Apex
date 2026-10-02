#!/bin/sh
# Starts the relay on a host like Railway: optionally joins your Tailscale
# network (so calls can be handed to your PC), keeps the answerer running next
# to the mailbox, and runs the mailbox in the foreground.
#
#   TS_AUTHKEY      optional; a Tailscale auth key (reusable + ephemeral)
#   TS_HOSTNAME     optional; the relay's name on your tailnet (default apex-relay)
#   RELAY_ANSWER    "off" to run the mailbox only (no model key on this host)
set -eu
cd "$(dirname "$0")"

if [ -n "${TS_AUTHKEY:-}" ]; then
  # Userspace networking: no special container privileges needed. The HTTP
  # proxy is how server.py reaches the PC's private https://<pc>.ts.net address.
  tailscaled --tun=userspace-networking --state=mem: \
             --outbound-http-proxy-listen=127.0.0.1:1055 >/tmp/tailscaled.log 2>&1 &
  tailscale up --auth-key="$TS_AUTHKEY" --hostname="${TS_HOSTNAME:-apex-relay}" --accept-dns=false
  export RELAY_PC_PROXY="${RELAY_PC_PROXY:-http://127.0.0.1:1055}"
  echo "[relay] joined the tailnet as ${TS_HOSTNAME:-apex-relay}"
fi

PORT_NOW="${RELAY_SERVER_PORT:-${PORT:-8799}}"
if [ "${RELAY_ANSWER:-on}" != "off" ]; then
  # The answerer talks to the mailbox over localhost, and is restarted if it stops.
  ( export RELAY_SERVER_URL="http://127.0.0.1:${PORT_NOW}"
    while true; do python3 answer.py --watch || true; echo "[relay] answerer stopped; restarting in 5s"; sleep 5; done ) &
fi

export RELAY_SERVER_HOST="${RELAY_SERVER_HOST:-0.0.0.0}"
exec python3 server.py
