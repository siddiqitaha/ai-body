"""Phase 6 tests: the real LocalScanner evaluator fails CLOSED in every no-verdict case."""
from __future__ import annotations

import os

from adapters import LocalScannerEvaluator
from ports import Decision


def test_denies_when_no_token():
    os.environ.pop("SCANNER_GATEWAY_TOKEN", None)
    v = LocalScannerEvaluator().evaluate("anything", {"stage": "pre"})
    assert v.decision == Decision.DENY and "token absent" in v.reason


def test_denies_when_gateway_unreachable():
    os.environ["SCANNER_GATEWAY_TOKEN"] = "dummy"
    try:
        v = LocalScannerEvaluator(endpoint="http://127.0.0.1:1/x", timeout_s=1.0)\
            .evaluate("x", {"stage": "pre"})
        assert v.decision == Decision.DENY and "unreachable" in v.reason
    finally:
        os.environ.pop("SCANNER_GATEWAY_TOKEN", None)


def test_maps_block_and_allow(monkeypatch=None):
    ev = LocalScannerEvaluator()
    os.environ["SCANNER_GATEWAY_TOKEN"] = "dummy"
    import adapters
    import io, json

    class FakeResp:
        def __init__(self, payload): self._p = json.dumps(payload).encode()
        def read(self): return self._p
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(payload):
        return lambda req, timeout=0: FakeResp(payload)

    try:
        adapters.urllib.request.urlopen = fake_urlopen({"action": "block", "reason": "danger"})
        assert ev.evaluate("bad", {"stage": "pre"}).decision == Decision.DENY
        adapters.urllib.request.urlopen = fake_urlopen({"action": "allow"})
        assert ev.evaluate("ok", {"stage": "pre"}).decision == Decision.ALLOW
        adapters.urllib.request.urlopen = fake_urlopen({"action": "weird"})
        assert ev.evaluate("?", {"stage": "pre"}).decision == Decision.DENY  # unknown -> deny
    finally:
        import importlib
        importlib.reload(adapters)
        os.environ.pop("SCANNER_GATEWAY_TOKEN", None)


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
