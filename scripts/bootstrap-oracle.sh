#!/usr/bin/env bash
# bootstrap-oracle.sh — run this once on a fresh Oracle Cloud Free Tier VM.
#
# What it does (fully unattended after Tailscale auth):
#   1. System packages
#   2. uv (Python package manager)
#   3. Tailscale (pauses for browser auth)
#   4. Clone Apex repo
#   5. Install systemd service
#   6. Start Apex
#
# Usage (from your LAPTOP after SSH into the Oracle VM):
#   ssh ubuntu@<oracle-public-ip>
#   curl -fsSL https://raw.githubusercontent.com/alexkhouryyy/ni/main/scripts/bootstrap-oracle.sh | bash
#
# Or copy-paste the whole file and run: bash bootstrap-oracle.sh

set -euo pipefail

REPO_URL="https://github.com/alexkhouryyy/ni.git"
REPO_BRANCH="main"
REPO_DIR="$HOME/ni"
USERNAME="$(whoami)"
SERVICE_NAME="apex@${USERNAME}"

GREEN="\033[0;32m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
NC="\033[0m"

step() { echo -e "\n${GREEN}[${1}]${NC} $2"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
die()  { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }

# -------------------------------------------------------
step "1/6" "System packages"
# -------------------------------------------------------
sudo apt-get update -qq
sudo apt-get install -y -qq \
    git curl python3-pip python3-venv \
    iptables netfilter-persistent iptables-persistent \
    sqlite3 build-essential

# -------------------------------------------------------
step "2/6" "uv (fast Python package manager)"
# -------------------------------------------------------
if ! command -v uv &>/dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # shellcheck disable=SC1090
    source "$HOME/.bashrc" 2>/dev/null || true
    export PATH="$HOME/.local/bin:$PATH"
fi
echo "uv: $(uv --version)"

# -------------------------------------------------------
step "3/6" "Tailscale"
# -------------------------------------------------------
if ! command -v tailscale &>/dev/null; then
    curl -fsSL https://tailscale.com/install.sh | sh
fi

TAILSCALE_IP="$(tailscale ip -4 2>/dev/null || true)"
if [ -z "$TAILSCALE_IP" ]; then
    echo ""
    echo "  Tailscale is installed but not authenticated."
    echo "  Running: sudo tailscale up"
    echo "  -> Copy the URL that appears, paste it in your browser, and approve."
    echo "  -> Then come back here and press Enter."
    echo ""
    sudo tailscale up || true
    read -rp "  Press Enter once Tailscale is authenticated..."
    TAILSCALE_IP="$(tailscale ip -4 2>/dev/null)" || die "Tailscale not connected."
fi
echo "Tailscale IP: $TAILSCALE_IP"

# -------------------------------------------------------
step "3b/6" "Firewall — allow only SSH + Tailscale"
# -------------------------------------------------------
sudo iptables -F INPUT 2>/dev/null || true
sudo iptables -A INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 22 -j ACCEPT
sudo iptables -A INPUT -i lo -j ACCEPT
sudo iptables -A INPUT -i tailscale0 -j ACCEPT
sudo iptables -A INPUT -j DROP
sudo netfilter-persistent save 2>/dev/null || true

# -------------------------------------------------------
step "4/6" "Clone Apex repo"
# -------------------------------------------------------
if [ -d "$REPO_DIR/.git" ]; then
    echo "Repo already cloned at $REPO_DIR — pulling latest..."
    git -C "$REPO_DIR" fetch origin
    git -C "$REPO_DIR" checkout "$REPO_BRANCH"
    git -C "$REPO_DIR" pull --ff-only origin "$REPO_BRANCH"
else
    git clone --branch "$REPO_BRANCH" "$REPO_URL" "$REPO_DIR"
fi

# -------------------------------------------------------
step "4b/6" "Install Python dependencies"
# -------------------------------------------------------
cd "$REPO_DIR"
uv pip install -r requirements.txt --quiet 2>/dev/null || \
    uv run python -c "import anthropic" 2>/dev/null || \
    uv sync --quiet 2>/dev/null || true
echo "Dependencies installed."

# -------------------------------------------------------
step "4c/6" ".env — copy yours from your laptop, then press Enter"
# -------------------------------------------------------
if [ ! -f "$REPO_DIR/.env" ]; then
    echo ""
    echo "  No .env found. Run this from your LAPTOP to transfer it:"
    echo ""
    echo "    scp $REPO_DIR/.env ${USERNAME}@${TAILSCALE_IP}:${REPO_DIR}/.env"
    echo ""
    echo "  The .env must contain at least: ANTHROPIC_API_KEY and DASHBOARD_TOKEN"
    echo "  Do NOT paste secrets into this terminal."
    echo ""
    read -rp "  Press Enter once you have transferred .env..."
    [ -f "$REPO_DIR/.env" ] || die ".env still missing — cannot start Apex without API keys."
fi

# -------------------------------------------------------
step "5/6" "systemd service"
# -------------------------------------------------------
UNIT_SRC="$REPO_DIR/scripts/apex.service"
UNIT_DST="/etc/systemd/system/${SERVICE_NAME}.service"

sudo cp "$UNIT_SRC" "$UNIT_DST"
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"

# -------------------------------------------------------
step "6/6" "Start Apex"
# -------------------------------------------------------
sudo systemctl restart "$SERVICE_NAME"

echo ""
sleep 3
if sudo systemctl is-active --quiet "$SERVICE_NAME"; then
    echo -e "${GREEN}Apex is running!${NC}"
else
    warn "Service did not start cleanly. Check logs:"
    echo "  journalctl -u ${SERVICE_NAME} -n 50"
    exit 1
fi

# Set PUBLIC_BASE_URL so push notifications use the correct address
if ! grep -q "PUBLIC_BASE_URL" "$REPO_DIR/.env" 2>/dev/null; then
    PORT="$(grep -Po '(?<=DASHBOARD_PORT=)\d+' "$REPO_DIR/.env" 2>/dev/null || echo 7860)"
    echo "PUBLIC_BASE_URL=http://${TAILSCALE_IP}:${PORT}" >> "$REPO_DIR/.env"
    sudo systemctl restart "$SERVICE_NAME"
    echo "Set PUBLIC_BASE_URL=http://${TAILSCALE_IP}:${PORT}"
fi

echo ""
echo "================================================="
echo "  Apex is live on Oracle Cloud"
echo "================================================="
echo ""
echo "  Dashboard (on Tailscale):  http://${TAILSCALE_IP}:7860"
echo "  Add your token:            http://${TAILSCALE_IP}:7860?token=YOUR_DASHBOARD_TOKEN"
echo ""
echo "  Check status:   systemctl status ${SERVICE_NAME}"
echo "  Live logs:      journalctl -u ${SERVICE_NAME} -f"
echo "  Restart:        sudo systemctl restart ${SERVICE_NAME}"
echo ""
echo "  Auto-starts on reboot. Test:"
echo "    sudo reboot && (wait 30s) && systemctl status ${SERVICE_NAME}"
echo ""
