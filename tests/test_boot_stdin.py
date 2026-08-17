"""Text mode must survive having no terminal attached.

The first time `python main.py --text` was actually booted, it wrote **129 MB
of "YOU: " in 90 seconds at 101% CPU**. `listen()` caught EOFError and returned
`""`; the main loop's answer to `""` is `continue`, which calls `input()` again,
which raises again. A hot spin that pins a core and fills the disk.

It needs no unusual setup to trigger — stdin is closed or empty under systemd,
nohup, docker, cron, and plain `&`. Every way an always-on agent is actually
started hits it on the very first read.

Two tests, and they check different things on purpose:

  * `test_eof_is_not_an_empty_turn` is behavioural, and is the one that fails
    without the fix.
  * `test_main_loop_handles_the_eof_sentinel` is structural. It cannot prove
    the loop is correct, only that the loop still looks at the sentinel — so
    the reader and the loop cannot drift apart, which is the way this fix
    would most plausibly be half-reverted. It is a guard, not a proof; the
    proof was a real boot (129 MB -> 1,875 bytes, 101% CPU -> 8%).
"""
from __future__ import annotations

import ast
import io
import pathlib
import sys

import pytest

import main

MAIN_PY = pathlib.Path(main.__file__)


def test_eof_is_not_an_empty_turn(monkeypatch):
    """EOF must be distinguishable from someone pressing enter.

    Both used to arrive as `""`. The loop treats `""` as "prompt again", so an
    EOF that returns `""` is an instruction to spin forever.
    """
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert main.read_text_input() is main._EOF


def test_pressing_enter_is_still_an_empty_turn(monkeypatch):
    """The fix must not turn a blank line into a shutdown."""
    monkeypatch.setattr("sys.stdin", io.StringIO("\nsomething\n"))
    assert main.read_text_input() == ""


def test_normal_input_is_unchanged(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("  hello apex  \n"))
    assert main.read_text_input() == "hello apex"


def test_sentinel_is_not_reachable_by_typing(monkeypatch):
    """The loop compares with `is`, so a typed lookalike must not be the
    sentinel object."""
    monkeypatch.setattr("sys.stdin", io.StringIO(main._EOF + "\n"))
    typed = main.read_text_input()
    assert typed is not main._EOF


def _text_branch() -> ast.If:
    """The `if args.text:` branch inside the main input loop."""
    tree = ast.parse(MAIN_PY.read_text())
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "main")
    # The main loop is the `while` whose test mentions shutdown_event.
    loop = next(n for n in ast.walk(fn)
                if isinstance(n, ast.While)
                and "shutdown_event" in ast.dump(n.test))
    for node in loop.body:
        if (isinstance(node, ast.If)
                and "args" in ast.dump(node.test)
                and "text" in ast.dump(node.test)):
            return node
    pytest.fail("could not find the `if args.text:` branch of the main loop")


def test_main_loop_handles_the_eof_sentinel():
    """The loop must react to the sentinel, not just receive it.

    A reader that reports EOF into a loop that ignores it is the original bug
    with extra steps.
    """
    branch = _text_branch()
    dumped = ast.dump(branch)
    assert "_EOF" in dumped, (
        "the text branch of the main loop no longer checks for the EOF "
        "sentinel — an EOF will fall through to `if not user_input: continue` "
        "and spin at 100% CPU"
    )
    # And it must stop reading rather than loop: either park on the shutdown
    # event or leave the loop.
    assert "shutdown_event" in dumped or any(
        isinstance(n, (ast.Break, ast.Return)) for n in ast.walk(branch)), (
        "the EOF branch neither waits on shutdown_event nor leaves the loop"
    )


def test_tui_mode_also_leaves_on_eof():
    """`--tui` has its own input loop and got this right from the start.
    Pinned so a future refactor cannot regress it back to `continue`."""
    src = pathlib.Path(main.__file__).parent / "tui" / "app.py"
    tree = ast.parse(src.read_text())
    handlers = [h for h in ast.walk(tree)
                if isinstance(h, ast.ExceptHandler)
                and "EOFError" in ast.dump(h.type or ast.Constant(None))]
    assert handlers, "tui/app.py no longer handles EOFError"
    assert any(isinstance(n, (ast.Break, ast.Return))
               for h in handlers for n in ast.walk(h)), (
        "tui's EOF handler no longer exits its input loop"
    )
