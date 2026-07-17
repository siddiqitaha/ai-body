"""Phase 7 tests: the Acquire funnel. An unscanned or tampered tool cannot run; a scan that
trips a guard is refused entry; a clean tool is admitted and invocable."""
from __future__ import annotations

from acquire import AcquireFunnel, GovernedTools, fingerprint
from adapters import RefEvaluator

SAFE_SPEC = "def status(args, caller): return {'ok': True}"
DIRTY_SPEC = "def leak(args, caller): return open('/x').read()  # aws_secret_access_key harvest"


def _funnel():
    return AcquireFunnel(evaluators=[RefEvaluator()])


def test_clean_tool_is_admitted_and_invocable():
    f = _funnel()
    adm = f.admit("status", SAFE_SPEC)
    assert adm.ok and adm.digest and adm.sandbox
    gt = GovernedTools(f)
    gt.register("status", SAFE_SPEC, lambda a, c: {"ok": True, "caller": c})
    assert gt.list() == ["status"]
    assert gt.invoke("status", {}, "taha")["ok"]


def test_unscanned_tool_is_denied():
    f = _funnel()
    gt = GovernedTools(f)
    gt.register("status", SAFE_SPEC, lambda a, c: {"ok": True})  # registered but NEVER admitted
    assert gt.list() == []
    try:
        gt.invoke("status", {}, "taha")
        assert False, "unadmitted tool must be denied"
    except PermissionError as e:
        assert "not admitted" in str(e)


def test_scan_refuses_a_dirty_spec():
    f = _funnel()
    adm = f.admit("leak", DIRTY_SPEC)
    assert not adm.ok and "refused" in adm.reason  # RefEvaluator saw the secret marker
    assert not f.is_admitted("leak", DIRTY_SPEC)


def test_tampered_tool_is_denied_at_invoke():
    """Admit a clean spec, then swap the running spec: the digest no longer matches -> deny."""
    f = _funnel()
    f.admit("status", SAFE_SPEC)
    gt = GovernedTools(f)
    gt.register("status", SAFE_SPEC + "  # SWAPPED after admission", lambda a, c: {"ok": True})
    assert gt.list() == []  # current spec != admitted digest
    try:
        gt.invoke("status", {}, "taha")
        assert False, "tampered tool must be denied"
    except PermissionError as e:
        assert "not admitted" in str(e)


def test_fingerprint_is_stable_and_specific():
    assert fingerprint(SAFE_SPEC) == fingerprint(SAFE_SPEC)
    assert fingerprint(SAFE_SPEC) != fingerprint(SAFE_SPEC + " ")


def test_scanner_error_refuses_entry():
    class Boom:
        name = "boom"
        def evaluate(self, s, c): raise RuntimeError("scanner down")
    f = AcquireFunnel(evaluators=[Boom()])
    adm = f.admit("status", SAFE_SPEC)
    assert not adm.ok and "errored" in adm.reason  # fail-closed on a broken scanner


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
