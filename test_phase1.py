"""Phase 1 tests: the governance wiring, provable without a live server.

The live-server walk is exercised by phase1.py. These lock the invariants that must hold
even when the control plane is DOWN: the HTTP evaluator fails CLOSED, the eval store records,
and doctor refuses to pass a stack with no provably-live gate.
"""
from __future__ import annotations

from adapters import ACEvaluator, RefEvaluator
from doctor import check
from heart import Heart, Registry, Trace
from manifest import Manifest
from observ import EvalStore
from ports import Decision


def test_ac_evaluator_fails_closed_when_server_unreachable():
    # point at a dead port: no server there -> must DENY, never allow-through
    ev = ACEvaluator("http://127.0.0.1:1", "aibody-core", timeout_s=1.0)
    v = ev.evaluate("anything at all", {"stage": "pre"})
    assert v.decision == Decision.DENY and "unreachable" in v.reason.lower()


def test_eval_store_records_and_counts():
    store = EvalStore()
    store.record(stage="pre", principal="taha", evaluator="x", decision="deny",
                 confidence=1.0, reason="r", subject="s")
    store.record(stage="pre", principal="taha", evaluator="x", decision="allow",
                 confidence=1.0, reason="r", subject="s")
    assert store.counts() == {"deny": 1, "allow": 1}
    assert len(store.all()) == 2


def test_heart_records_every_verdict():
    reg, store = Registry(), EvalStore()
    reg.register(Manifest("evaluator", "ref-dlp"), RefEvaluator())
    heart = Heart(reg, Trace(), eval_store=store)
    heart._gate("clean text", {"stage": "pre", "principal": "taha"})
    heart._gate("has SECRET_MARKER in it", {"stage": "pre", "principal": "taha"})
    assert store.counts().get("allow") == 1 and store.counts().get("deny") == 1


def test_doctor_fails_when_no_gate_proven_live():
    """A stack whose only evaluator never denies the bad canary must FAIL doctor (nonzero)."""
    reg = Registry()

    class NeverDeny(RefEvaluator):
        name = "never-deny"
        MARKERS = ()  # matches nothing -> never denies

    reg.register(Manifest("evaluator", "never-deny"), NeverDeny())
    assert check(reg, verbose=False) != 0


def test_doctor_fails_when_gate_is_down():
    """A dead-port AC denies EVERYTHING (including the benign canary) = the server is DOWN.
    doctor must catch that (good-canary denied) and fail, not mistake down for healthy."""
    reg = Registry()
    reg.register(Manifest("evaluator", "agent-control"),
                 ACEvaluator("http://127.0.0.1:1", "aibody-core", timeout_s=1.0))
    assert check(reg, verbose=False) != 0


def test_doctor_passes_with_a_genuinely_live_gate():
    """A healthy gate denies the bad canary and allows the good one -> doctor passes."""
    from ports import EvaluatorPort, Verdict

    class HealthyGate(EvaluatorPort):
        name = "healthy"
        def evaluate(self, subject, context):
            bad = any(m in str(subject).lower() for m in (".env", "id_rsa", ".ssh/"))
            return Verdict(Decision.DENY if bad else Decision.ALLOW, reason="canary")

    reg = Registry()
    reg.register(Manifest("evaluator", "healthy"), HealthyGate())
    assert check(reg, verbose=False) == 0


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
