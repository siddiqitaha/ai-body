"""Characterization tests for the walking skeleton. Run: python3 -m pytest test_skeleton.py -q
(or python3 test_skeleton.py for a stdlib-only run with no pytest).

These lock in the Phase 0 definition-of-done: end-to-end walk, fail-closed gate, deny-by-default
door, tighten-only verdict bus, and the modularity test (second adapter = zero core changes).
"""
from __future__ import annotations

from adapters import LedgerMemory, LocalModel, LocalSurface, RefEvaluator, StatusTool
from heart import Heart, Registry, Trace
from manifest import Manifest
from ports import Decision, EvaluatorPort, Verdict


def _build():
    reg = Registry()
    reg.register(Manifest("model", "primary"), LocalModel())
    reg.register(Manifest("memory", "ledger"), LedgerMemory())
    reg.register(Manifest("tool", "status", tools=["status"]), StatusTool())
    reg.register(Manifest("evaluator", "ref-dlp"), RefEvaluator())
    heart = Heart(reg, Trace())
    door = LocalSurface(heart.handle, "t")
    reg.register(Manifest("surface", "local-door"), door)
    return heart, door, reg


def _req(intent, text, **x):
    return {"token": "t", "intent": intent, "text": text, **x}


def test_end_to_end_walk():
    _, door, _ = _build()
    assert door.receive(_req("remember", "the sky is blue today"), "taha")["ok"]
    hits = door.receive(_req("recall", "sky"), "taha")["hits"]
    assert any("sky" in h["text"] for h in hits)


def test_memory_scope_isolation():
    _, door, _ = _build()
    door.receive(_req("remember", "alice private note"), "alice")
    hits = door.receive(_req("recall", "alice"), "bob")["hits"]
    assert hits == []  # bob cannot see alice's scope


def test_door_denies_missing_principal():
    _, door, _ = _build()
    assert door.receive(_req("ask", "hi"), None)["ok"] is False


def test_door_rejects_bad_token():
    _, door, _ = _build()
    assert door.receive({"intent": "ask", "text": "hi", "token": "WRONG"}, "taha")["ok"] is False


def test_dlp_denies_planted_secret():
    _, door, _ = _build()
    r = door.receive(_req("remember", "here is SECRET_MARKER value"), "taha")
    assert r.get("blocked") and r["decision"] == "deny"


def test_gate_fails_closed_on_broken_evaluator():
    """Kill the evaluator (make it raise): an action must be DENIED, not allowed."""
    heart, door, reg = _build()

    class Broken(EvaluatorPort):
        name = "broken"
        def evaluate(self, subject, context):
            raise RuntimeError("evaluator down")

    reg.register(Manifest("evaluator", "broken"), Broken())
    r = door.receive(_req("ask", "anything"), "taha")
    assert r.get("blocked") and r["decision"] == "deny"


def test_tighten_only_bus():
    """A DENY evaluator must win over an ALLOW one regardless of order."""
    heart, door, reg = _build()

    class AlwaysDeny(EvaluatorPort):
        name = "always-deny"
        def evaluate(self, subject, context):
            return Verdict(Decision.DENY, reason="policy")

    reg.register(Manifest("evaluator", "always-deny"), AlwaysDeny())
    assert door.receive(_req("ask", "hi"), "taha").get("decision") == "deny"


def test_modularity_second_model_row_no_core_change():
    """The native-modularity test: a second model adapter is a register() call, zero core edits."""
    _, _, reg = _build()
    before = set(reg.models)
    reg.register(Manifest("model", "secondary"), LocalModel(model="heavy"))
    assert set(reg.models) - before == {"secondary"}


def test_tool_unknown_denied():
    tool = StatusTool()
    try:
        tool.invoke("rm-rf", {}, "taha")
        assert False, "unknown tool should deny"
    except PermissionError:
        pass


def test_supersede_and_invalidate_append_only():
    mem = LedgerMemory()
    a = mem.remember("v1 fact", "user:x")
    b = mem.supersede(a, "v2 fact", "taha", "correction")
    hits = mem.recall("fact", 5, "user:x")
    texts = [h["text"] for h in hits]
    assert "v2 fact" in texts and "v1 fact" not in texts  # old superseded, not returned
    mem.invalidate(b, "taha", "forget")
    assert mem.recall("fact", 5, "user:x") == []


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
            passed += 1
        except Exception:
            print(f"FAIL {t.__name__}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} passed")
