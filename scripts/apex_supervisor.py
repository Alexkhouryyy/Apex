"""Keep Apex running, and start it again when it asks to be.

`Apex.bat` does this in batch with a `goto` loop, because it already owns a
console and can. Resident mode cannot: `start-apex.bat` launches with `pyw` and
exits immediately so there is no window, which also means there is nothing left
behind to notice the process stopped. Without this, the dashboard's Restart
button would work in the mode with a terminal and refuse in the mode you
actually leave running — half a feature, and the confusing half.

The contract is one number. Apex exits with `control.EXIT_RESTART` (42) when it
wants to come back, and anything else means stop:

  * 0             — you quit. Stay quit.
  * 42            — restart requested from the dashboard.
  * anything else — it crashed. **Do not restart**, or a crash during boot
                    becomes an infinite respawn that eats the machine while
                    looking, from outside, exactly like Apex running fine.

That last one is the whole reason this is not `while True: run()`.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.control import EXIT_RESTART, SUPERVISOR_ENV  # noqa: E402

# A restart loop that spins is worse than no restart loop: it hides the failure
# behind constant activity. Even a legitimate 42 gets a floor, and a burst of
# them stops the loop entirely.
MIN_SECONDS_BETWEEN_STARTS = 2.0
MAX_RESTARTS_PER_WINDOW = 5
WINDOW_SECONDS = 120.0


def should_restart(code: int) -> bool:
    """Only an explicit request. Kept separate so it can be tested."""
    return code == EXIT_RESTART


def run(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:]) or ["--resident"]
    env = dict(os.environ)
    env[SUPERVISOR_ENV] = "1"

    starts: list[float] = []
    while True:
        now = time.time()
        starts = [t for t in starts if now - t < WINDOW_SECONDS]
        if len(starts) >= MAX_RESTARTS_PER_WINDOW:
            print(f"[supervisor] {len(starts)} restarts in "
                  f"{WINDOW_SECONDS:.0f}s — refusing to keep looping.",
                  file=sys.stderr)
            return 1
        starts.append(now)

        proc = subprocess.run([sys.executable, str(ROOT / "main.py"), *args],
                              cwd=str(ROOT), env=env)
        if not should_restart(proc.returncode):
            return proc.returncode
        print("[supervisor] restart requested.", file=sys.stderr)
        time.sleep(MIN_SECONDS_BETWEEN_STARTS)


if __name__ == "__main__":
    raise SystemExit(run())
