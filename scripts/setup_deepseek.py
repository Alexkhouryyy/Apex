"""Configure DeepSeek V4.1 Flash without putting a secret in shell history."""
from getpass import getpass
from pathlib import Path
import shutil
from datetime import datetime
import argparse
from dotenv import dotenv_values

from set_env_key import set_key


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--use-existing-key", action="store_true")
    args = parser.parse_args()
    env = Path(__file__).resolve().parents[1] / ".env"
    if env.exists():
        text = env.read_text(encoding="utf-8-sig")
        if "\x00" in text:
            raise SystemExit("The Apex .env contains null bytes. Repair its encoding first; nothing changed.")
    key = ((dotenv_values(env).get("DEEPSEEK_API_KEY") or "")
           if args.use_existing_key else getpass("DeepSeek API key (hidden): ")).strip()
    if not key or any(c in key for c in "\r\n\x00"):
        raise SystemExit("No valid key supplied; nothing changed.")
    if env.exists():
        backup = env.with_name(".env.before-deepseek-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
        shutil.copy2(env, backup)
    set_key(env, "DEEPSEEK_API_KEY", key)
    set_key(env, "AGENT_MODEL", "deepseek-flash")
    set_key(env, "BACKGROUND_MODEL", "deepseek-flash")
    for name in ("PROACTIVE_MODEL", "CONSTELLATION_PLANET_MODEL",
                 "CONSTELLATION_MEMORY_MODEL", "CONSTELLATION_SYNTH_MODEL",
                 "TIME_CAPSULE_MODEL", "GUARDIAN_MODELS", "SAFETY_REVIEW_MODEL"):
        set_key(env, name, "deepseek-flash")
    set_key(env, "DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    print("Saved: DeepSeek V4.1 Flash is the conversation and automatic reasoning model.")
    print("Prior settings backed up; other credentials and memory paths retained.")
    print("Launch: .venv\\Scripts\\python.exe main.py --text")
    print("Switch during a session: /model MODEL_ID. Startup default stays deepseek-flash.")


if __name__ == "__main__":
    main()
