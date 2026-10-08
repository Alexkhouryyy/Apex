"""Terminal UI with streaming output and interrupt-and-redirect.

Replaces the bare input()/print() text mode. Built on prompt_toolkit:
  - rich line editing + in-session history at the prompt
  - agent turns stream token-by-token above the prompt (via patch_stdout)
  - submitting a new line while a turn is running INTERRUPTS that turn and
    redirects to the new message
  - Ctrl-C interrupts a running turn; Ctrl-C when idle exits

Entry point: run_tui(agent).
"""
import threading

from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout

from tui.streamer import TUIStreamer

_BANNER = """\
============================================================
  Apex — Terminal UI
  Type a message and press Enter. While the agent is
  responding you can type again to interrupt and redirect.
  Ctrl-C interrupts a running turn; Ctrl-C when idle quits.
  Type 'quit' to exit.
============================================================"""


def run_tui(agent) -> None:
    """Run the interactive terminal UI against a live AgentCore instance."""
    print(_BANNER)
    session: PromptSession = PromptSession(history=InMemoryHistory())
    state: dict = {"thread": None, "cancel": None}

    def _turn_running() -> bool:
        t = state["thread"]
        return t is not None and t.is_alive()

    def _interrupt_current() -> None:
        if _turn_running():
            state["cancel"].set()
            state["thread"].join(timeout=10)

    def _start_turn(text: str) -> None:
        cancel = threading.Event()
        streamer = TUIStreamer(cancel)

        def _worker():
            streamer.start()
            try:
                agent.run(text, include_screenshot=False, streamer=streamer,
                          cancel_event=cancel, withhold=frozenset(), by_owner=True)
            except Exception as exc:
                print(f"\n[error] {exc}")
            finally:
                streamer.finish()

        t = threading.Thread(target=_worker, daemon=True, name="TUITurn")
        state["thread"], state["cancel"] = t, cancel
        t.start()

    kb = KeyBindings()

    @kb.add("c-c")
    def _(event):
        if _turn_running():
            state["cancel"].set()
            print("\n[interrupted]")
        else:
            event.app.exit(exception=EOFError)

    with patch_stdout():
        while True:
            try:
                text = session.prompt("YOU: ", key_bindings=kb)
            except (EOFError, KeyboardInterrupt):
                break
            text = (text or "").strip()
            if not text:
                continue
            if text.lower() in {"quit", "exit", "bye", "goodbye"}:
                break
            if _turn_running():
                print("[interrupting current turn — redirecting]")
                _interrupt_current()
            _start_turn(text)

    _interrupt_current()
    print("\n[TUI closed]")
