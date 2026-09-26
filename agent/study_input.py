"""One expiring owner for study gestures, shared with the board's pause gate."""
import threading
import time
from agent import assembly

LOCK = threading.RLock()
TTL = 2.0
_lease = None


def active():
    global _lease
    with LOCK:
        if _lease and _lease['until'] <= time.monotonic():
            _lease = None
        return dict(_lease) if _lease else None


def control(sid, owner, action):
    global _lease
    if not isinstance(owner, str) or not 16 <= len(owner) <= 80:
        raise ValueError('Invalid study controller id.')
    with LOCK:
        lease = active()
        mine = lease and lease['session'] == sid and lease['owner'] == owner
        if action == 'release':
            if mine:
                _lease = None
            return {'released': True}
        if action not in ('claim', 'sample'):
            raise ValueError('Unknown hand-control action.')
        assembly.state(sid)
        if action == 'sample' and not mine:
            raise ValueError('Study hand control expired. Enable hands again.')
        if lease and not mine:
            raise ValueError('Another study window controls hands. Pause it before switching.')
        if action == 'claim':
            from agent.board import get_board
            get_board().set_hands_enabled(False)
        _lease = dict(session=sid, owner=owner, until=time.monotonic() + TTL)
        from agent.handtrack import active_tracker
        tracker = active_tracker()
        sample = tracker.study_sample() if tracker else {'sequence': 0, 'age_ms': None, 'hands': []}
        return dict(tracking=tracker is not None, **sample)
