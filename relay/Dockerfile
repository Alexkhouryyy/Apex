# The relay on Railway (or any container host): standard-library Python plus
# Tailscale's own binaries, copied from their official image. Build context is
# the repository root (relay/railway.toml).
FROM python:3.11-slim
COPY --from=docker.io/tailscale/tailscale:stable /usr/local/bin/tailscaled /usr/local/bin/tailscale /usr/local/bin/
WORKDIR /relay
COPY relay/server.py relay/answer.py relay/start.sh ./
ENV PYTHONUNBUFFERED=1 RELAY_SERVER_DB=/data/relay.db
CMD ["sh", "start.sh"]
