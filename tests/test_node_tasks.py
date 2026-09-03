"""Delegated work, and the four ways a queue quietly goes wrong.

Step 5 of docs/PHASE_6_7_PLAN.md. The plan's success check is two clauses, and
both have a class here: "an expired lease requeues" (`TestLeasesExpire`) and
"a task for an offline node reads as waiting, never as done"
(`TestATaskForASleepingLaptop`).

`TestTheQueueCarriesARequestNotAnApproval` is the one that matters most and
tests the least code. A queue that could carry permission with it would undo the
whole permission model in one column, and it would look like a feature.
"""
import time

import pytest

from agent import capabilities as caps
from agent import longterm
from agent import node_tasks as nt


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(longterm, "DB_PATH", str(tmp_path / "tasks.db"))
    nt.init_db()
    caps.init_db()


def _able(node, cap, state=caps.YES, age=0.0):
    """Record a capability directly, as a probe on that node would have."""
    with longterm._conn() as c:
        c.execute("INSERT OR REPLACE INTO device_capabilities "
                  "(device_id, name, state, detail, verified_at) "
                  "VALUES (?, ?, ?, 'test', ?)",
                  (node, cap, state, time.time() - age))
        c.commit()


class TestTheQueueCarriesARequestNotAnApproval:
    def test_there_is_no_approval_column(self, db):
        """A delegated command runs through the node's own safety.check,
        mcp_policy.enforce and subagent_scope.check at execution time. A queue
        that could say "already approved" would bypass all three."""
        with longterm._conn() as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(node_tasks)")}
        for forbidden in ("approved", "approval", "permitted", "allow",
                          "authorised", "authorized", "confirmed"):
            assert forbidden not in cols, (
                f"node_tasks has an '{forbidden}' column. Permission belongs to "
                f"the node at execution time, never to the queue.")

    def test_submit_takes_no_permission_argument(self):
        import inspect
        params = set(inspect.signature(nt.submit).parameters)
        assert not (params & {"approved", "approval", "permitted", "confirm"})


class TestATaskForASleepingLaptop:
    """The success check's second clause. Waiting is the correct state, and the
    laptop opening its lid resolves it — so this must be neither an error nor a
    completion."""

    def test_it_is_queued_not_failed(self, db):
        _able("laptop", "blender", caps.YES, age=caps.MAX_AGE_SECONDS + 60)
        t = nt.submit("render", capability="blender", node="laptop")
        assert nt.get(t["id"])["status"] == nt.QUEUED

    def test_it_never_reads_as_done(self, db):
        """Asserted against the wording `describe` actually uses for a finished
        task, not against the substring "done" — the waiting message contains
        "nothing has been done to it", so a naive check passes on the very text
        that proves the opposite."""
        _able("laptop", "blender", caps.YES, age=caps.MAX_AGE_SECONDS + 60)
        t = nt.submit("render", capability="blender", node="laptop")
        text = nt.describe(t["id"])
        assert "waiting for laptop" in text
        assert "finished" not in text
        assert nt.get(t["id"])["status"] not in (nt.DONE, nt.DEAD, nt.FAILED)

    def test_it_says_nothing_has_been_done_to_it(self, db):
        t = nt.submit("render", node="laptop")
        assert "Nothing has been done to it" in nt.describe(t["id"])

    def test_a_stale_capability_does_not_block_submission(self, db):
        """An offline laptop's records are stale BY DEFINITION — it has not been
        there to probe. Refusing on staleness would refuse every task the moment
        the laptop sleeps, which is the exact case this queue exists for."""
        _able("laptop", "blender", caps.YES, age=caps.MAX_AGE_SECONDS * 10)
        assert nt.submit("render", capability="blender", node="laptop")["id"]

    def test_a_stale_no_does_not_block_either(self, db):
        """Blender might have been installed since. The honest answer is that we
        will find out when the machine comes back."""
        _able("laptop", "blender", caps.NO, age=caps.MAX_AGE_SECONDS * 10)
        assert nt.submit("render", capability="blender", node="laptop")["id"]

    def test_it_waits_until_the_node_can_actually_do_it(self, db):
        _able("laptop", "blender", caps.YES, age=caps.MAX_AGE_SECONDS + 60)
        t = nt.submit("render", capability="blender", node="laptop")
        assert nt.claim("laptop") is None, "a stale capability is not usable"
        _able("laptop", "blender", caps.YES, age=0)      # the laptop wakes
        got = nt.claim("laptop")
        assert got and got["id"] == t["id"]


class TestAFreshNoIsRefusedImmediately:
    def test_it_refuses_rather_than_queuing_forever(self, db):
        """"Blender is not installed there" does not become true by waiting, and
        telling you now beats a task nobody ever looks at again."""
        _able("laptop", "blender", caps.NO, age=0)
        with pytest.raises(nt.TaskRefused) as e:
            nt.submit("render", capability="blender", node="laptop")
        assert "cannot do 'blender'" in str(e.value)

    def test_the_refusal_says_when_it_was_checked(self, db):
        _able("laptop", "blender", caps.NO, age=0)
        with pytest.raises(nt.TaskRefused) as e:
            nt.submit("render", capability="blender", node="laptop")
        assert "ago" in str(e.value)

    def test_an_unknown_capability_is_not_a_refusal(self, db):
        """`unknown` means the probe failed, not that the thing is absent."""
        _able("laptop", "blender", caps.UNKNOWN, age=0)
        assert nt.submit("render", capability="blender", node="laptop")["id"]


class TestLeasesExpire:
    """The success check's first clause. A node that claims a task and dies
    holds work nobody else will touch, and nothing has to notice the crash."""

    def test_an_expired_lease_returns_the_task_to_the_queue(self, db):
        _able("laptop", "blender")
        t = nt.submit("render", capability="blender")
        assert nt.claim("laptop", lease_seconds=60)["id"] == t["id"]
        assert nt.get(t["id"])["status"] == nt.CLAIMED

        later = time.time() + 61
        assert nt.sweep(later)["requeued"] == 1
        assert nt.get(t["id"])["status"] == nt.QUEUED

    def test_another_node_can_then_take_it(self, db):
        _able("laptop", "blender")
        _able("desktop", "blender")
        t = nt.submit("render", capability="blender")
        nt.claim("laptop", lease_seconds=60)
        later = time.time() + 61
        got = nt.claim("desktop", now=later)
        assert got and got["id"] == t["id"] and got["attempt"] == 2

    def test_a_live_lease_is_not_stolen(self, db):
        _able("laptop", "blender")
        _able("desktop", "blender")
        nt.submit("render", capability="blender")
        nt.claim("laptop", lease_seconds=600)
        assert nt.claim("desktop") is None

    def test_finishing_after_the_lease_expired_is_refused(self, db):
        """Someone else may be running it now. Writing a result for work that is
        no longer yours is how two nodes both succeed at one task."""
        _able("laptop", "blender")
        t = nt.submit("render", capability="blender")
        nt.claim("laptop", lease_seconds=60)
        later = time.time() + 61
        nt.sweep(later)
        assert nt.complete(t["id"], "laptop", "done!", now=later) is False
        assert nt.get(t["id"])["status"] == nt.QUEUED

    def test_the_lease_countdown_is_reported(self, db):
        _able("laptop", "blender")
        t = nt.submit("render", capability="blender")
        nt.claim("laptop", lease_seconds=600)
        text = nt.describe(t["id"])
        assert "running on laptop" in text and "returns to the queue" in text


class TestRetriesAreBounded:
    def test_out_of_attempts_it_dies_rather_than_looping(self, db):
        """An infinite retry loop wearing a feature's clothes."""
        _able("laptop", "blender")
        t = nt.submit("render", capability="blender", max_attempts=2)
        for _ in range(2):
            nt.claim("laptop")
            nt.fail(t["id"], "laptop", "blender crashed")
        assert nt.get(t["id"])["status"] == nt.DEAD

    def test_a_dead_task_keeps_why(self, db):
        """A dead task that threw away its reason makes the next person
        reproduce it."""
        _able("laptop", "blender")
        t = nt.submit("render", capability="blender", max_attempts=1)
        nt.claim("laptop")
        nt.fail(t["id"], "laptop", "the addon socket refused")
        assert "socket refused" in nt.get(t["id"])["error"]
        assert "socket refused" in nt.describe(t["id"])

    def test_a_lease_that_expires_repeatedly_also_dies(self, db):
        """The crash case, not the raise case: a node that keeps dying mid-task
        must not requeue forever."""
        _able("laptop", "blender")
        t = nt.submit("render", capability="blender", max_attempts=2)
        now = time.time()
        for _ in range(2):
            nt.claim("laptop", lease_seconds=10, now=now)
            now += 11
            nt.sweep(now)
        assert nt.get(t["id"])["status"] == nt.DEAD
        assert "never finished" in nt.get(t["id"])["error"]

    def test_a_failure_with_attempts_left_goes_back_to_the_queue(self, db):
        _able("laptop", "blender")
        t = nt.submit("render", capability="blender", max_attempts=3)
        nt.claim("laptop")
        assert nt.fail(t["id"], "laptop", "transient") is True
        assert nt.get(t["id"])["status"] == nt.QUEUED


class TestANodeNeverClaimsWhatItCannotDo:
    def test_a_capable_node_gets_it_and_an_incapable_one_does_not(self, db):
        _able("laptop", "blender", caps.YES)
        _able("phone", "blender", caps.NO)
        t = nt.submit("render", capability="blender")
        assert nt.claim("phone") is None
        assert nt.claim("laptop")["id"] == t["id"]

    def test_a_task_with_no_capability_requirement_is_open_to_anyone(self, db):
        t = nt.submit("ping")
        assert nt.claim("some-random-node")["id"] == t["id"]

    def test_a_targeted_task_is_not_taken_by_another_node(self, db):
        nt.submit("render", node="laptop")
        assert nt.claim("desktop") is None
        assert nt.claim("laptop") is not None

    def test_nothing_able_is_reported_as_such(self, db):
        """"Waiting for the laptop" and "waiting for a machine that does not
        exist" are the same row and different problems."""
        t = nt.submit("render", capability="hologram")
        assert "no known node currently can" in nt.describe(t["id"])


class TestOneWinnerPerTask:
    def test_two_claimers_do_not_both_get_it(self, db):
        """The UPDATE is the lock. Checking then writing would let two nodes
        both believe they won."""
        _able("a", "blender")
        _able("b", "blender")
        nt.submit("render", capability="blender")
        first, second = nt.claim("a"), nt.claim("b")
        assert (first is None) != (second is None)

    def test_a_node_cannot_finish_a_task_it_does_not_hold(self, db):
        _able("a", "blender")
        _able("b", "blender")
        t = nt.submit("render", capability="blender")
        nt.claim("a")
        assert nt.complete(t["id"], "b", "not mine") is False
        assert nt.get(t["id"])["status"] == nt.CLAIMED


class TestCompletion:
    def test_a_finished_task_says_where_it_ran(self, db):
        _able("laptop", "blender")
        t = nt.submit("render", capability="blender")
        nt.claim("laptop")
        assert nt.complete(t["id"], "laptop", "wrote chair.glb") is True
        assert nt.get(t["id"])["result"] == "wrote chair.glb"
        assert "finished on laptop" in nt.describe(t["id"])

    def test_a_finished_task_stops_being_pending(self, db):
        _able("laptop", "blender")
        t = nt.submit("render", capability="blender")
        nt.claim("laptop")
        nt.complete(t["id"], "laptop", "ok")
        assert [p["id"] for p in nt.pending()] == []

    def test_pending_shows_queued_and_running_but_not_finished(self, db):
        _able("laptop", "blender")
        a = nt.submit("one", capability="blender")
        b = nt.submit("two", capability="blender")
        nt.claim("laptop")
        nt.complete(a["id"], "laptop", "ok")
        assert [p["id"] for p in nt.pending()] == [b["id"]]


class TestSubmissionValidation:
    def test_a_task_needs_a_kind(self, db):
        with pytest.raises(nt.TaskRefused):
            nt.submit("   ")

    def test_an_unknown_task_describes_itself_as_absent(self, db):
        assert "no task 999" in nt.describe(999)
