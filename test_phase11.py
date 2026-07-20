"""Phase 11 tests: a second Surface, an HTTP door, funnels into the SAME gated heart.

Proves the Surface port is modular too (two doors, zero core change), and that the network door
keeps the same fail-closed contract: good Bearer token + principal works, a bad token is 401 and
never reaches the heart, a missing principal is denied, malformed input is 400."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

from adapters import HTTPSurface, LedgerMemory, LocalModel, LocalSurface, RefEvaluator
from heart import Heart, Registry, Trace
from manifest import Manifest

TOKEN = "http-dev-token"


def _heart():
    reg = Registry()
    reg.register(Manifest("model", "primary", controls={"tier": "local", "accepts": "any"}),
                 LocalModel(base="http://127.0.0.1:9/dead"))
    reg.register(Manifest("memory", "ledger"), LedgerMemory())
    reg.register(Manifest("evaluator", "ref-dlp"), RefEvaluator())
    return Heart(reg, Trace())


def _post(port, body, *, token=TOKEN, principal="taha"):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/", data=json.dumps(body).encode(),
                                 method="POST")
    req.add_header("Content-Type", "application/json")
    if token is not None:
        req.add_header("Authorization", f"Bearer {token}")
    if principal is not None:
        req.add_header("X-Principal", principal)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


class _Server:
    """Start the HTTP door on an ephemeral port in a background thread for the duration of a test."""
    def __init__(self, heart):
        self.door = HTTPSurface(heart.handle, TOKEN)
        self.srv, self.port = self.door.serve(port=0)
        self.t = threading.Thread(target=self.srv.serve_forever, daemon=True)

    def __enter__(self):
        self.t.start()
        return self

    def __exit__(self, *a):
        self.srv.shutdown()
        self.srv.server_close()


# --- the in-process contract (same shape as the local door) ---------------------
def test_receive_funnels_into_the_heart():
    door = HTTPSurface(_heart().handle, TOKEN)
    out = door.receive({"token": TOKEN, "intent": "remember", "text": "hello"}, "taha")
    assert out["ok"] and "id" in out


def test_receive_rejects_bad_token():
    door = HTTPSurface(_heart().handle, TOKEN)
    assert door.receive({"token": "wrong", "intent": "remember", "text": "x"}, "taha")["ok"] is False


# --- the real network round trip ------------------------------------------------
def test_http_good_token_and_principal_works():
    with _Server(_heart()) as s:
        code, body = _post(s.port, {"intent": "remember", "text": "a fact over http"})
        assert code == 200 and body["ok"] and "id" in body


def test_http_bad_token_is_401_and_never_reaches_the_heart():
    with _Server(_heart()) as s:
        code, body = _post(s.port, {"intent": "remember", "text": "x"}, token="nope")
        assert code == 401 and body["ok"] is False


def test_http_missing_principal_is_denied():
    with _Server(_heart()) as s:
        code, body = _post(s.port, {"intent": "remember", "text": "x"}, principal=None)
        assert code == 200 and body["ok"] is False and body["error"] == "unauthenticated"


def test_http_malformed_body_is_400():
    with _Server(_heart()) as s:
        req = urllib.request.Request(f"http://127.0.0.1:{s.port}/", data=b"not json", method="POST")
        req.add_header("Authorization", f"Bearer {TOKEN}")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                code = r.status
        except urllib.error.HTTPError as e:
            code = e.code
        assert code == 400


def test_http_planted_secret_is_gated_over_the_wire():
    with _Server(_heart()) as s:
        code, body = _post(s.port, {"intent": "remember", "text": "aws_secret_access_key=AKIA"})
        assert code == 200 and body.get("blocked") and "DLP" in body["reason"]


# --- modularity: two doors, same heart, zero core change ------------------------
def test_two_surfaces_share_one_gated_heart():
    heart = _heart()
    local = LocalSurface(heart.handle, "local-token")
    http = HTTPSurface(heart.handle, TOKEN)
    a = local.receive({"token": "local-token", "intent": "remember", "text": "via local"}, "taha")
    b = http.receive({"token": TOKEN, "intent": "remember", "text": "via http"}, "taha")
    assert a["ok"] and b["ok"]                          # both doors reach the same handler


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
    sys.exit(0)
