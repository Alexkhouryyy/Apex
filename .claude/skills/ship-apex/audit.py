"""Revert-audit: prove a test guards a line. Breaks the line (OLD -> NEW, first
occurrence), runs the given tests, always restores the file, and reports
CAUGHT (a test failed, good) or GAP (everything still passed: add a test).

    python .claude/skills/ship-apex/audit.py app/follow.py "if focus" "if False" tests/test_companion_follow.py
"""
import subprocess
import sys
from pathlib import Path


def main(argv):
    if len(argv) < 4:
        print(__doc__)
        return 2
    path, old, new, *tests = argv
    file = Path(path)
    original = file.read_bytes()
    text = original.decode('utf-8')
    if old not in text:
        print(f'NOT FOUND in {path}: {old!r}')
        return 2
    try:
        file.write_bytes(text.replace(old, new, 1).encode('utf-8'))
        result = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-x', '-p', 'no:cacheprovider', *tests],
                                capture_output=True, text=True)
    finally:
        file.write_bytes(original)
    last = (result.stdout.strip().splitlines() or ['?'])[-1]
    caught = result.returncode != 0
    print(f'{"CAUGHT" if caught else "GAP   "}  {path}: {old[:60]!r} -> {new[:40]!r}  [{last}]')
    return 0 if caught else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
