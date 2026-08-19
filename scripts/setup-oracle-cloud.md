# Set up Apex on Oracle Cloud Free Tier (always-on brain)

Oracle Cloud's Always Free tier gives you a **2 OCPU / 12 GB RAM Ampere A1 VM —
genuinely free forever**. This is the always-on host for Apex so the autonomous
cortex, scheduler, and dashboard never sleep.

---

## 1. Create your Oracle Cloud account

1. Go to `cloud.oracle.com` -> **Start for free**
2. Enter name, email, region (pick one near you — US East or UK South are reliable)
3. Credit card required for verification only — you will not be charged
4. Confirm email -> account created

---

## 2. Provision the free VM

1. Oracle Cloud Console -> **Compute -> Instances -> Create Instance**
2. Click **Change image** -> **Ubuntu** -> Ubuntu 22.04 LTS (aarch64)
3. Click **Change shape** -> **Ampere** -> `VM.Standard.A1.Flex`
   - Set **OCPUs: 2**, **Memory: 12 GB** (both free-tier eligible)
4. **Networking**: leave defaults (new VCN + public subnet is fine)
5. **Add SSH keys**: paste your `~/.ssh/id_ed25519.pub` (generate with `ssh-keygen -t ed25519` if needed)
6. Click **Create** — VM boots in about 2 minutes

Note the **public IP** from the instance details page.

---

## 3. Run the one-command bootstrap (does everything else automatically)

SSH in once, then run the bootstrap script. It installs Tailscale, clones Apex,
sets up the firewall, creates the systemd service, and starts Apex.

```bash
# From your laptop — SSH into the Oracle VM
ssh ubuntu@<oracle-public-ip>

# On the Oracle VM — run the bootstrap (one command)
curl -fsSL https://raw.githubusercontent.com/alexkhouryyy/ni/main/scripts/bootstrap-oracle.sh | bash
```

The script will **pause twice** and tell you exactly what to do:

**Pause 1 — Tailscale auth:**
It prints a URL. Open it in your browser, click Approve. Press Enter in the terminal.

**Pause 2 — Transfer .env:**
Run this from a SECOND terminal on your laptop (not the Oracle terminal):
```bash
scp ~/ni/.env ubuntu@<oracle-public-ip>:~/ni/.env
```
Then press Enter in the Oracle terminal.

After those two steps the script finishes unattended. Apex starts automatically.

---

## 4. Verify Apex is running

Once the bootstrap completes:
```bash
# Check service status
systemctl status apex@ubuntu

# Live logs
journalctl -u apex@ubuntu -f

# Open dashboard from your phone (on Tailscale)
http://<oracle-tailscale-ip>:7860?token=YOUR_DASHBOARD_TOKEN
```

The Tailscale IP is printed by the bootstrap script. If Apex is running you will
see the dashboard load even while your laptop is off.

---

## 5. Sync the Obsidian vault (laptop <-> cloud)

The vault at `~/Documents/Apex` needs to stay in sync between your Windows laptop
and the Oracle VM so Apex's notes are consistent everywhere.

From your laptop, run:
```bash
# Push your laptop's vault to the cloud (after Tailscale is running on both)
bash scripts/sync-vault.sh push

# Pull the cloud vault to your laptop
bash scripts/sync-vault.sh pull

# Auto-push every 5 minutes (run in background while working)
bash scripts/sync-vault.sh watch
```

If the script cannot find the Oracle VM automatically, set the IP explicitly:
```bash
ORACLE_IP=100.x.x.x bash scripts/sync-vault.sh push
```

---

## 6. Keeping Apex up to date

To pull new code onto the Oracle VM:
```bash
ssh ubuntu@<oracle-tailscale-ip>
cd ~/ni
git pull origin main
sudo systemctl restart apex@ubuntu
```

Or run the bootstrap script again — it detects an existing repo and just pulls.

---

## Hybrid mode (laptop awareness + cloud always-on)

The Oracle VM handles: autonomous cortex, scheduler, dashboard, goals, reflections.
Your laptop handles: screen watching, clipboard, microphone (awareness events).

Both share the same `DASHBOARD_TOKEN`. The laptop's Apex and the cloud's Apex
run independently — the cloud is the always-on brain, the laptop is the perceptual layer.

---

## Verification checklist

- [ ] `systemctl status apex@ubuntu` shows **active (running)**
- [ ] Dashboard loads at `http://<tailscale-ip>:7860?token=...` from your phone on cellular (laptop off)
- [ ] `sudo reboot` -> wait 30s -> `systemctl status apex@ubuntu` still running (auto-restart works)
- [ ] `bash scripts/sync-vault.sh push` copies vault without errors
