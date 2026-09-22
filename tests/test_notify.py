"""Tests for the Notification Hub: subscription store, dedup, fan-out, push pruning."""
import os
import sys
import tempfile
import types
import unittest

import config
from agent import longterm

SUB = {"endpoint": "https://push.example/abc", "keys": {"p256dh": "BPabc", "auth": "xyz"}}


def _fresh_db():
    d = tempfile.mkdtemp()
    longterm.DB_PATH = os.path.join(d, "n.db")
    longterm.init_db()
    from agent import notify
    notify.init_push_table()


class TestSubscriptionStore(unittest.TestCase):
    def setUp(self):
        self._orig = longterm.DB_PATH
        _fresh_db()

    def tearDown(self):
        longterm.DB_PATH = self._orig

    def test_add_list_remove(self):
        from agent import notify
        sid = notify.add_subscription(SUB, "phone")
        self.assertGreater(sid, 0)
        subs = notify.list_subscriptions()
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0]["device_label"], "phone")
        notify.remove_subscription(SUB["endpoint"])
        self.assertEqual(len(notify.list_subscriptions()), 0)

    def test_upsert_on_same_endpoint(self):
        from agent import notify
        notify.add_subscription(SUB, "first")
        notify.add_subscription(SUB, "second")
        subs = notify.list_subscriptions()
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0]["device_label"], "second")

    def test_missing_keys_raises(self):
        from agent import notify
        with self.assertRaises(ValueError):
            notify.add_subscription({"endpoint": "x"})


class TestDedup(unittest.TestCase):
    def setUp(self):
        """Same reason as TestFanout._notifier: dedup is measured by counting
        what reached a sink, and a held notification reaches none of them. On a
        machine where restraint has learned to be quiet, that reads as perfect
        deduplication."""
        was = getattr(config, "RESTRAINT_ENABLED", True)
        config.RESTRAINT_ENABLED = False
        self.addCleanup(setattr, config, "RESTRAINT_ENABLED", was)

    def test_same_key_suppressed_within_window(self):
        from agent.notify import Notifier
        config.NOTIFY_DEDUP_SECONDS = 30
        n = Notifier()
        calls = []
        n._ws_broadcast = lambda p: calls.append(p)
        n._web_push = lambda p: 1
        n.notify("t", "b", dedup_key="k1")
        n.notify("t", "b", dedup_key="k1")  # suppressed
        self.assertEqual(len(calls), 1)
        n.notify("t", "b", dedup_key="k2")  # different key fires
        self.assertEqual(len(calls), 2)

    def test_no_key_never_deduped(self):
        from agent.notify import Notifier
        n = Notifier()
        calls = []
        n._ws_broadcast = lambda p: calls.append(p)
        n._web_push = lambda p: 1
        n.notify("t", "b")
        n.notify("t", "b")
        self.assertEqual(len(calls), 2)


class TestFanout(unittest.TestCase):
    def _notifier(self):
        """Fanout, with restraint out of the way.

        These tests are about which sinks a payload reaches. `notify()` asks
        `restraint.should_hold` first, and restraint LEARNS from the real
        memory database — so once a machine has accumulated enough ignored
        pings, it starts holding notifications and four of these tests fail
        with no code change anywhere near them. Apex learning to be quiet broke
        its own test suite, which is a good sign for restraint and a bad one
        for a test that depended on machine state.

        `restraint.hold_all` in tests/test_restraint.py is where holding is
        tested; here it is disabled so the fanout is what is being measured.
        """
        import config
        from agent.notify import Notifier
        self._restraint_was = getattr(config, "RESTRAINT_ENABLED", True)
        config.RESTRAINT_ENABLED = False
        self.addCleanup(setattr, config, "RESTRAINT_ENABLED", self._restraint_was)
        n = Notifier()
        n._ws_broadcast = lambda p: None
        return n

    def test_telegram_fallback_when_no_push(self):
        n = self._notifier()
        n._web_push = lambda p: 0
        tg = []
        n._telegram = lambda t, b: tg.append((t, b))
        config.NOTIFY_TELEGRAM_FALLBACK = True
        n.notify("T", "B")
        self.assertEqual(len(tg), 1)

    def test_no_telegram_when_push_delivered(self):
        n = self._notifier()
        n._web_push = lambda p: 3
        tg = []
        n._telegram = lambda t, b: tg.append((t, b))
        config.NOTIFY_TELEGRAM_FALLBACK = True
        n.notify("T", "B")
        self.assertEqual(len(tg), 0)

    def test_tray_called_when_set(self):
        n = self._notifier()
        n._web_push = lambda p: 1
        seen = []
        n.set_tray(lambda t, b: seen.append((t, b)))
        n.notify("Hi", "there")
        self.assertEqual(seen, [("Hi", "there")])

    def test_sink_error_does_not_raise(self):
        n = self._notifier()
        def boom(p):
            raise RuntimeError("ws down")
        n._ws_broadcast = boom
        n._web_push = lambda p: 1
        n.notify("T", "B")  # must not raise


class TestWebPushPrune(unittest.TestCase):
    def setUp(self):
        self._orig = longterm.DB_PATH
        self._orig_key = config.VAPID_PRIVATE_KEY
        _fresh_db()

    def tearDown(self):
        longterm.DB_PATH = self._orig
        config.VAPID_PRIVATE_KEY = self._orig_key
        sys.modules.pop("pywebpush", None)

    def test_prunes_dead_subscription_on_410(self):
        from agent import notify
        notify.add_subscription({"endpoint": "https://e/1", "keys": {"p256dh": "a", "auth": "b"}})
        notify.add_subscription({"endpoint": "https://e/2", "keys": {"p256dh": "a", "auth": "b"}})

        class WPE(Exception):
            def __init__(self, resp=None):
                self.response = resp

        class _Resp:
            status_code = 410

        def fake_webpush(subscription_info, data, vapid_private_key, vapid_claims):
            if subscription_info["endpoint"].endswith("/1"):
                raise WPE(_Resp())
            return True

        fake = types.ModuleType("pywebpush")
        fake.webpush = fake_webpush
        fake.WebPushException = WPE
        sys.modules["pywebpush"] = fake

        config.VAPID_PRIVATE_KEY = "dummy"
        config.VAPID_SUBJECT = "mailto:a@b.com"
        n = notify.Notifier()
        sent = n._web_push({
            "title": "t", "body": "b", "kind": "info",
            "priority": "normal", "url": "/", "dedup_key": None,
        })
        self.assertEqual(sent, 1)  # only /2 delivered
        subs = notify.list_subscriptions()
        self.assertEqual(len(subs), 1)
        self.assertTrue(subs[0]["endpoint"].endswith("/2"))

    def test_no_push_without_vapid_key(self):
        from agent import notify
        config.VAPID_PRIVATE_KEY = ""
        n = notify.Notifier()
        self.assertEqual(n._web_push({"title": "t", "body": "b", "kind": "info",
                                      "priority": "normal", "url": "/"}), 0)


if __name__ == "__main__":
    unittest.main()
