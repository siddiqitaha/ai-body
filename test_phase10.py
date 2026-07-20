"""Phase 10 tests: tier-aware model routing (private-by-default, fail-closed).

Sensitive calls stay on a local tier; non-sensitive calls may offload to a cloud tier; a sensitive
call with no local tier is refused rather than leaked; and an untagged fleet routes exactly as
before (back-compat). The cloud adapter itself refuses secret-bearing input as a second line."""
from __future__ import annotations

from adapters import CloudModel, LedgerMemory, LocalModel, LocalSurface, RefEvaluator
from heart import Heart, Registry, Trace
from manifest import Manifest
from router import RouteDenied, choose_model

LOCAL = Manifest("model", "primary", controls={"tier": "local", "accepts": "any"})
CLOUD = Manifest("model", "cloud", controls={"tier": "cloud", "accepts": "non-sensitive"})


# --- the pure policy ------------------------------------------------------------
def test_sensitive_call_routes_to_local_tier():
    assert choose_model({"primary": LOCAL, "cloud": CLOUD}, sensitive=True) == "primary"


def test_nonsensitive_call_offloads_to_cloud_tier():
    assert choose_model({"primary": LOCAL, "cloud": CLOUD}, sensitive=False) == "cloud"


def test_sensitive_with_only_cloud_is_denied_not_leaked():
    try:
        choose_model({"cloud": CLOUD}, sensitive=True)
        assert False, "sensitive data must never route to a cloud tier"
    except RouteDenied:
        pass


def test_untagged_fleet_is_unchanged_backcompat():
    plain = Manifest("model", "primary")              # no tier declared
    assert choose_model({"primary": plain}, sensitive=True) == "primary"
    assert choose_model({"primary": plain}, sensitive=False) == "primary"


def test_deterministic_tie_break_by_id():
    a = Manifest("model", "a", controls={"tier": "local"})
    b = Manifest("model", "b", controls={"tier": "local"})
    assert choose_model({"b": b, "a": a}, sensitive=True) == "a"   # id-sorted, stable


# --- the heart wiring end to end ------------------------------------------------
def _heart(with_cloud: bool):
    reg = Registry()
    reg.register(LOCAL, LocalModel(base="http://127.0.0.1:9/dead"))
    if with_cloud:
        reg.register(CLOUD, CloudModel(base="http://127.0.0.1:9/dead"))
    reg.register(Manifest("memory", "ledger"), LedgerMemory())
    reg.register(Manifest("evaluator", "ref-dlp"), RefEvaluator())
    heart = Heart(reg, Trace())
    reg.register(Manifest("surface", "local-door"), LocalSurface(heart.handle, "t"))
    return heart, reg


def _req(text, **x):
    return {"token": "t", "intent": "ask", "text": text, **x}


def test_heart_picks_local_for_sensitive_cloud_for_nonsensitive():
    heart, _ = _heart(with_cloud=True)
    assert heart._pick_model(sensitive=True).capabilities()["local"] is True
    assert heart._pick_model(sensitive=False).capabilities()["local"] is False


def _chosen(heart):
    return next(s["chosen"] for s in reversed(heart.trace.spans) if s["name"] == "route.model")


def test_door_defaults_to_private_local_tier():
    """A request with no `sensitive` flag is treated as private -> local tier."""
    heart, _ = _heart(with_cloud=True)
    r = heart.handle(_req("hello"), "taha")
    assert r["ok"] and _chosen(heart) == "primary"     # routed to the local tier


def test_door_opt_out_routes_to_cloud_tier():
    heart, _ = _heart(with_cloud=True)
    r = heart.handle(_req("public docs question", sensitive=False), "taha")
    assert r["ok"] and _chosen(heart) == "cloud"       # opted out -> cloud tier


def test_cloud_adapter_refuses_secret_bearing_input():
    """Second line of defense: even if mis-routed, the cloud tier will not process private markers."""
    cm = CloudModel(base="http://127.0.0.1:9/dead")
    try:
        cm.complete([{"role": "user", "content": "here is aws_secret_access_key=AKIA"}])
        assert False, "cloud tier must refuse secret-bearing input"
    except PermissionError:
        pass


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
    sys.exit(0)
