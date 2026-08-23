"""The pinch calibrator's judgement, which has to be willing to say "I can't".

The camera half is unproven here and always will be — there is no webcam on any
machine that runs this suite. What IS tested is the part that decides, because a
calibrator that always emits a number is worse than no calibrator: it converts
"the readings did not separate" into a confident setting that misfires for ever
while looking measured.

`HANDTRACK_PINCH_RATIO` is the single number deciding whether pinch works at all,
and a wrong value fails silently — the gesture is simply never recognized and
nothing anywhere says why.
"""
import pytest

from scripts import calibrate_pinch as cal


def _spread(centre: float, n: int = 60, width: float = 0.06) -> list:
    """A plausible cloud of readings around a centre, deterministically.

    Real samples jitter; a list of identical values would let a broken
    percentile pass, so the fixture has to have width.
    """
    return [centre + width * ((i % 11) - 5) / 5.0 for i in range(n)]


class TestClean:
    @pytest.mark.parametrize("junk", [
        None, "0.4", True, False, float("nan"), float("inf"), float("-inf"),
    ])
    def test_unusable_readings_are_dropped(self, junk):
        """pinch_ratio returns None for landmarks it cannot use, and a NaN would
        pass an isinstance check while poisoning every comparison after it —
        silently, since NaN comparisons are False rather than an error."""
        assert cal.clean([0.5, junk, 0.6]) == [0.5, 0.6]

    def test_booleans_are_not_numbers_here(self):
        """`True` is an int in Python and would read as a ratio of 1.0."""
        assert cal.clean([True, False]) == []

    @pytest.mark.parametrize("empty", [None, [], ()])
    def test_nothing_in_nothing_out(self, empty):
        assert cal.clean(empty) == []


class TestPercentile:
    def test_median_of_a_known_set(self):
        assert cal.percentile([1, 2, 3, 4, 5], 50) == 3

    def test_the_tails(self):
        vals = list(range(101))
        assert cal.percentile(vals, 5) == pytest.approx(5, abs=1)
        assert cal.percentile(vals, 95) == pytest.approx(95, abs=1)

    def test_a_single_reading(self):
        assert cal.percentile([0.42], 95) == 0.42

    def test_empty_raises_rather_than_inventing_a_number(self):
        with pytest.raises(ValueError):
            cal.percentile([], 50)


class TestRecommendThreshold:
    def test_clean_separation_lands_in_the_gap(self):
        value, reason = cal.recommend_threshold(_spread(1.8), _spread(0.2))
        assert value is not None
        assert 0.2 < value < 1.8, f"{value} is not between the clouds"
        assert "separated" in reason.lower()

    def test_the_threshold_is_biased_toward_catching_pinches(self):
        """Missing a pinch looks like a broken gesture; a slightly eager pinch
        does not. So the boundary sits nearer the open-hand side of the gap."""
        value, _ = cal.recommend_threshold(_spread(1.6), _spread(0.3))
        midpoint = (0.3 + 1.6) / 2
        assert value > midpoint, "a lazy pinch would not register"

    def test_overlapping_clouds_refuse(self):
        """THE load-bearing test. Two distributions whose tails cross cannot be
        separated by any threshold, and emitting one anyway would produce a
        setting that is wrong in both directions while looking calibrated."""
        value, reason = cal.recommend_threshold(_spread(0.55), _spread(0.50))
        assert value is None
        assert "overlap" in reason.lower() or "cannot be separated" in reason.lower()

    def test_far_apart_medians_still_refuse_when_the_tails_cross(self):
        """Separation is judged on the tails, not the medians — that is the
        whole reason this is not `(mean(open) + mean(pinch)) / 2`. A wide, noisy
        pinch cloud can have a distant median and still reach into the open one.
        """
        wide_pinch = _spread(0.4, width=0.9)     # median low, tail high
        value, reason = cal.recommend_threshold(_spread(1.2, width=0.2), wide_pinch)
        assert value is None, reason

    def test_too_few_readings_refuse_and_say_it_is_not_a_threshold_problem(self):
        value, reason = cal.recommend_threshold([1.8] * 5, [0.2] * 5)
        assert value is None
        assert "not enough" in reason.lower()
        assert "light" in reason.lower() or "closer" in reason.lower(), \
            "must point at the real cause rather than the threshold"

    def test_a_noise_width_gap_refuses(self):
        """Ordered is not the same as separated.

        Found by running the calibrator against a plausible "hand not opened
        properly" case: it returned 0.55 for a gap of 0.02 and called it
        fragile. Two hundredths is the width of the measurement itself — the
        ordering could reverse on the next frame. Calling that fragile
        understates it, and the whole point of this function is that it does not
        emit confident wrong answers.
        """
        value, reason = cal.recommend_threshold(
            _spread(0.62, width=0.06), _spread(0.48, width=0.06))
        assert value is None, f"returned {value} for a noise-width gap"
        assert "noise" in reason.lower()

    def test_a_thin_but_real_gap_returns_a_value_and_warns(self):
        """Separated is not the same as robust. Silently returning a fragile
        number is how it works on the day you tuned it and not afterwards."""
        value, reason = cal.recommend_threshold(
            _spread(0.60, width=0.01), _spread(0.50, width=0.01))
        assert value is not None, reason
        assert "fragile" in reason.lower()

    def test_one_empty_side_refuses(self):
        """A pinch that was never detected is not a pinch reading of zero."""
        assert cal.recommend_threshold(_spread(1.8), [])[0] is None
        assert cal.recommend_threshold([], _spread(0.2))[0] is None

    def test_identical_distributions_refuse(self):
        same = _spread(0.7)
        assert cal.recommend_threshold(same, same)[0] is None

    def test_junk_readings_do_not_manufacture_a_recommendation(self):
        """If every reading is unusable, the count check must catch it — NaN
        must not slip through and produce a number out of nothing."""
        junk = [float("nan")] * 60
        assert cal.recommend_threshold(junk, junk)[0] is None

    def test_the_result_is_usable_as_a_threshold(self):
        """End-to-end statement: whatever comes back must actually classify the
        two clouds correctly through the real code path."""
        from agent import handtrack
        import types

        def hand(ratio):
            lm = lambda x, y: types.SimpleNamespace(x=x, y=y, z=0.0)
            lms = [lm(0.0, 0.0) for _ in range(21)]
            lms[handtrack.WRIST] = lm(0.5, 0.75)
            lms[handtrack.MIDDLE_MCP] = lm(0.5, 0.5)
            lms[handtrack.INDEX_TIP] = lm(0.5, 0.5)
            lms[handtrack.THUMB_TIP] = lm(0.5 + 0.25 * ratio, 0.5)
            return lms

        value, _ = cal.recommend_threshold(_spread(1.8), _spread(0.2))
        assert handtrack.landmarks_to_cursor(hand(0.2), threshold=value)[2] is True
        assert handtrack.landmarks_to_cursor(hand(1.8), threshold=value)[2] is False


class TestDescribe:
    def test_it_reports_spread_not_just_a_number(self):
        """The spread is what tells you whether to trust the result, so it has
        to be on screen next to it."""
        out = cal.describe("open", _spread(1.5))
        assert "median" in out and "range" in out

    def test_no_readings_says_so(self):
        assert "no readings" in cal.describe("pinched", [])


class TestExplainNoReadings:
    """Zero readings has three causes needing three different fixes, and the
    first live run reported "try better light" without knowing which it was.

    Advice for the wrong failure is worse than none: it sends someone to fix a
    thing that was never broken. On 2026-08-23 this printed a lighting hint for
    a run where the camera may have delivered nothing at all.
    """

    def test_no_frames_is_a_capture_problem_not_a_hand_problem(self):
        out = cal.explain_no_readings({"frames": 0, "dropped": 120, "hands": 0},
                                      {"frames": 0, "dropped": 118, "hands": 0})
        assert "no frames" in out.lower()
        assert "not a hand problem" in out.lower()
        assert "opencv" in out.lower(), "must name the likeliest cause"
        assert "light" not in out.lower(), \
            "lighting advice here sends you to fix the wrong thing"

    def test_frames_but_no_hands_is_light_or_framing(self):
        out = cal.explain_no_readings({"frames": 120, "dropped": 0, "hands": 0},
                                      {"frames": 118, "dropped": 0, "hands": 0})
        assert "camera works" in out.lower()
        assert "light" in out.lower() or "framing" in out.lower()

    def test_hands_but_no_ratios_is_the_pose(self):
        """Landmarks arriving but pinch_ratio refusing them means a degenerate
        span — a hand seen edge-on, where wrist and knuckle collapse together."""
        out = cal.explain_no_readings({"frames": 120, "dropped": 0, "hands": 60},
                                      {"frames": 118, "dropped": 0, "hands": 55})
        assert "squarely" in out.lower() or "edge-on" in out.lower()

    def test_the_three_causes_read_differently(self):
        """The whole point. Three failures that used to produce one sentence
        must now produce three."""
        cases = [
            ({"frames": 0, "dropped": 5, "hands": 0},) * 2,
            ({"frames": 100, "dropped": 0, "hands": 0},) * 2,
            ({"frames": 100, "dropped": 0, "hands": 50},) * 2,
        ]
        said = {cal.explain_no_readings(a, b) for a, b in cases}
        assert len(said) == 3, said

    @pytest.mark.parametrize("junk", [{}, {"frames": None}])
    def test_missing_stats_do_not_raise(self, junk):
        """This runs on the failure path — raising here would replace a
        diagnosis with a traceback."""
        try:
            cal.explain_no_readings(junk, junk)
        except TypeError:
            pytest.fail("must survive incomplete stats")
