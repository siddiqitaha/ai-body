"""Phase 8 tests: the tier-2 out-of-process gateway. The load-bearing property is that when the
gate blocks, the upstream model is NEVER called. Offline: fake gate + a forward spy."""
from __future__ import annotations

from gateway import LLMGateway
from ports import Decision, Verdict


class FakeGate:
    """A stand-in EvaluatorPort. Denies anything containing 'bad'; else allows."""
    def __init__(self, unreachable=False):
        self.unreachable = unreachable
    def evaluate(self, subject, context):
        if self.unreachable:   # mimic ACEvaluator's fail-closed-when-unreachable
            return Verdict(Decision.DENY, reason="Agent Control unreachable")
        return Verdict(Decision.DENY if "bad" in str(subject).lower() else Decision.ALLOW,
                       reason="fake gate")


def _gw(gate):
    gw = LLMGateway(gate)
    gw._calls = []
    def spy(payload):
        gw._calls.append(payload)
        return {"choices": [{"message": {"content": "model answer"}}]}
    gw._forward = spy
    return gw


def _req(text):
    return {"model": "heavy", "messages": [{"role": "user", "content": text}]}


def test_allowed_prompt_reaches_model():
    gw = _gw(FakeGate())
    status, body = gw.handle(_req("hello there"))
    assert status == 200 and body["choices"][0]["message"]["content"] == "model answer"
    assert len(gw._calls) == 1 and gw.forwarded == 1


def test_blocked_prompt_never_calls_model():
    gw = _gw(FakeGate())
    status, body = gw.handle(_req("do something bad"))
    assert status == 403 and "tier 2" in body["error"]
    assert gw._calls == [] and gw.blocked == 1        # the model was NEVER reached


def test_gate_unreachable_fails_closed():
    gw = _gw(FakeGate(unreachable=True))
    status, body = gw.handle(_req("perfectly benign"))
    assert status == 403 and gw._calls == []          # outage blocks, model not called


def test_response_is_gated_on_egress():
    gate = FakeGate()
    gw = LLMGateway(gate)
    # allow the prompt, but the model returns something the gate denies on the way out
    gw._forward = lambda p: {"choices": [{"message": {"content": "this is bad output"}}]}
    status, body = gw.handle(_req("clean prompt"))
    assert status == 403 and "response blocked" in body["error"]


def test_upstream_error_is_502_not_a_leak():
    gw = LLMGateway(FakeGate())
    def boom(p): raise RuntimeError("model down")
    gw._forward = boom
    status, body = gw.handle(_req("clean"))
    assert status == 502 and "upstream error" in body["error"]


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
