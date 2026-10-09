"""Task-local procedural views. Stable metaskills persist; generated patches do not."""
import contextvars
from contextlib import contextmanager

_active = contextvars.ContextVar("task_procedure", default="")

METASKILL = (
    "Write a task-specific procedure only after identifying the current inputs, "
    "constraints and acceptance checks. Keep values and file paths task-local. "
    "Check execution against the fixed contract; preserve supporting and contradicting "
    "traces. Choose NO_PATCH for retrieval, permission, perception or capability failures. "
    "A reviewer's confidence cannot authorize a change. Discard this task's procedure "
    "after completion; persistent changes require independent held-out evaluation."
)


@contextmanager
def procedure(text):
    if not isinstance(text, str) or not text or len(text) > 12000:
        raise ValueError("a bounded task-local procedure is required")
    token = _active.set(text)
    try:
        yield text
    finally:
        _active.reset(token)


def current():
    return _active.get()


def run(task, generate, validate, execute, *, support_traces):
    """Generate and independently validate one ephemeral procedure, then discard it.

    Host callbacks own model execution, fixed checks and cost accounting. The
    generated text supplies procedure hints, never tool permissions or thresholds.
    No task artifact is persisted by this loop; durable promotion is separate.
    """
    if not isinstance(task, str) or not task or not support_traces:
        raise ValueError("a task and canonical supporting trace IDs are required")
    candidate = generate(METASKILL, task, tuple(support_traces))
    if not isinstance(candidate, str) or not candidate or len(candidate) > 12000:
        raise ValueError("generator must return a bounded task-local procedure")
    if validate(task, candidate, tuple(support_traces)) is not True:
        return {"status": "NO_PATCH", "reason": "fixed_validation_failed"}
    with procedure(candidate):
        result = execute(task)
    return {"status": "executed", "result": result, "support_traces": list(support_traces),
            "persisted": False}
