"""Phase 2 tests: the guard model's observe/enforce split and the calibration promotion gate.
No live model needed , a fake judge stands in; the live run is calibrate.py."""
from __future__ import annotations

import calibrate
from adapters import GuardModelEvaluator
from ports import Decision, Verdict


class FakeGuard(GuardModelEvaluator):
    """Judge by keyword, or raise, without any HTTP."""
    def __init__(self, mode="observe", broken=False):
        super().__init__(mode=mode)
        self.broken = broken

    def _judge(self, text: str) -> bool:
        if self.broken:
            raise RuntimeError("model down")
        return "attack" in text.lower()


def test_observe_mode_never_blocks():
    v = FakeGuard("observe").evaluate("this is an attack", {})
    assert v.decision == Decision.WARN and not v.blocks
    v = FakeGuard("observe").evaluate("benign text", {})
    assert v.decision == Decision.LOG


def test_observe_mode_fails_open_on_error():
    v = FakeGuard("observe", broken=True).evaluate("anything", {})
    assert v.decision == Decision.LOG and "unavailable" in v.reason


def test_enforce_mode_denies_and_raises():
    assert FakeGuard("enforce").evaluate("an attack here", {}).decision == Decision.DENY
    assert FakeGuard("enforce").evaluate("benign", {}).decision == Decision.ALLOW
    try:
        FakeGuard("enforce", broken=True).evaluate("anything", {})
        assert False, "enforce mode must raise so the bus fails closed"
    except RuntimeError:
        pass


def test_calibration_counts_se_sp():
    labeled = [("attack one", True), ("attack two", True), ("hello", False), ("weather", False)]
    r = calibrate.run(FakeGuard("observe"), labeled)
    assert (r["tp"], r["fn"], r["tn"], r["fp"]) == (2, 0, 2, 0)
    assert r["se"] == 1.0 and r["sp"] == 1.0


def test_promotion_refused_below_min_labels():
    r = calibrate.run(FakeGuard("observe"), [("attack", True), ("hi", False)])
    ok, msg = calibrate.promote(r)
    assert not ok and "labels" in msg


def test_promotion_refused_on_weak_se():
    class Blind(FakeGuard):
        def _judge(self, text): return False  # never flags anything
    labeled = [(f"attack {i}", True) for i in range(30)] + [(f"note {i}", False) for i in range(30)]
    ok, msg = calibrate.promote(calibrate.run(Blind("observe"), labeled))
    assert not ok and "Se" in msg


def test_promotion_passes_good_evaluator_at_scale():
    labeled = [(f"attack {i}", True) for i in range(30)] + [(f"note {i}", False) for i in range(30)]
    ok, msg = calibrate.promote(calibrate.run(FakeGuard("observe"), labeled))
    assert ok and "PROMOTED" in msg


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        try:
            t(); print(f"PASS {t.__name__}"); passed += 1
        except Exception:
            print(f"FAIL {t.__name__}"); traceback.print_exc()
    print(f"\n{passed}/{len(tests)} passed")
