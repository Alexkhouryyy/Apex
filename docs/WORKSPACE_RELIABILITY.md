# Workspace reliability pass — 2026-09-26

This maintenance pass follows merged PRs #3–#7. Céline's speed is satisfactory
in Alex's actual use; voice latency is not a target of this pass.

## Database contention

Main's post-merge run 36239540661 failed while extracting research notes;
several parallel telemetry inserts also logged `database is locked`.
Research extraction already collects notes on one thread, but its eight model
workers each record usage concurrently. Both short write transactions now use
one in-process write gate, held through commit/close. Model calls remain
parallel and readers are not gated. The existing SQLite timeout is unchanged.

The regression uses real SQLite, deliberately slow commits and a 1 ms busy
limit. Without the gate it reproduces the failing notes insert and lost usage;
with the gate all 24 model usage records and 48 grounded notes survive. A
separate check verifies rollback and release on error. This addresses these
participating writers, not every possible database lock from other modules or
external processes. It does not add retries or suppress storage errors.

## Paused controls and returning from study

The HANDS guidance now checks the actual paused state before giving gesture
instructions. The persistent toolbar action reads **Resume board hands**.
Study has an explicit **Workspace** return link; returning explains the paused
state. Resuming is a deliberate click using the existing workspace-context
check and exclusive study-control gate. Returning, lease expiry, and switching
workspaces never implicitly turn on gestures. A study in another tab can still
block resume until its controls are paused.

## Study hand sampling

The next request is scheduled against a 30 Hz target including elapsed request
and processing time, instead of waiting an additional 70 ms after each reply.
There is one in-flight sample per active controller. Slow requests lower the
rate naturally without queued catch-up requests. Old responses and failures
are ignored after pause/re-enable; the existing dwell, identity, stale-frame,
lease and cancellation rules remain in force.

The controlled-clock check compares 4 ms and 50 ms request times: the previous
loop's intervals were 74 ms and 120 ms; the new loop's intervals are about
33.3 ms and 50 ms. These are scheduling checks, not measured camera performance
or end-to-end gesture latency. Real Lenovo gesture comfort remains to be tested.
