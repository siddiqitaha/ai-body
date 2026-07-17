"""Phase 1: governed + monitored. Same skeleton, but the guard bus now calls the LIVE
Agent Control server on :19381 as an evaluator, every verdict is recorded in the eval store,
and the trace is exported to the local OTLP collector (fail-open).

The skeleton gets its OWN namespaced Agent Control agent ('aibody-core') + its own control,
so it never touches the openclaw:* agents. setup_ac() is idempotent (409 = already there).

Env needed at runtime:
  AIBODY_AC_ADMIN_KEY  - admin key, for one-time setup_ac() (agent + control create)
  AIBODY_AC_KEY        - non-admin key, for the runtime evaluation calls
"""
from __future__ import annotations

import json
import os
import urllib.request

from adapters import (
    ACEvaluator,
    GuardModelEvaluator,
    LedgerMemory,
    LocalModel,
    LocalSurface,
    RefEvaluator,
    StatusTool,
)
from heart import Heart, Registry, Trace
from manifest import Manifest
from observ import EvalStore, export_otlp

AC_BASE = "http://127.0.0.1:19381"
AIBODY_AGENT = "aibody-core"
DOOR_TOKEN = "skeleton-dev-token"
SECRETS_REGEX = r"(BEGIN RSA PRIVATE KEY|aws_secret_access_key|SECRET_MARKER|id_rsa|\.ssh/|\.env\b)"


def _post(path: str, body: dict, admin: bool, method: str = "POST") -> tuple[int, dict]:
    key = os.environ.get("AIBODY_AC_ADMIN_KEY" if admin else "AIBODY_AC_KEY", "")
    req = urllib.request.Request(AC_BASE + path, data=json.dumps(body).encode() if body else None,
                                 method=method)
    req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("X-API-Key", key)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:200]}


def setup_ac() -> None:
    """Register the skeleton's own agent + secrets control on the live server. Idempotent."""
    from datetime import UTC, datetime
    s, _ = _post("/api/v1/agents/initAgent", {
        "agent": {"agent_name": AIBODY_AGENT,
                  "agent_description": "AI Body walking-skeleton core",
                  "agent_created_at": datetime.now(UTC).isoformat()},
        "steps": [], "conflict_mode": "overwrite"}, admin=True)
    print(f"agent initAgent -> {s}")

    s, body = _post("/api/v1/controls", {
        "name": "aibody-deny-secrets",
        "data": {"enabled": True, "execution": "server", "scope": {"stages": ["pre", "post"]},
                 "condition": {"selector": {"path": "input"},
                               "evaluator": {"name": "regex", "config": {"pattern": SECRETS_REGEX}}},
                 "action": {"decision": "deny"}}}, admin=True, method="PUT")
    print(f"control create -> {s} {body.get('control_id', body)}")
    cid = body.get("control_id")
    if cid is None:  # already exists: find it by name
        st, lst = _post("/api/v1/controls", {}, admin=True, method="GET") if False else (0, {})
        # fall back: list controls to resolve the id
        req = urllib.request.Request(AC_BASE + "/api/v1/controls", method="GET")
        req.add_header("X-API-Key", os.environ.get("AIBODY_AC_ADMIN_KEY", ""))
        with urllib.request.urlopen(req, timeout=15) as r:
            items = json.loads(r.read().decode())
        rows = items.get("controls", items if isinstance(items, list) else [])
        # the LIST endpoint keys rows as 'id'; create returns 'control_id'. Accept either.
        cid = next((c.get("id", c.get("control_id")) for c in rows
                    if c.get("name") == "aibody-deny-secrets"), None)
    if cid is not None:
        s, _ = _post(f"/api/v1/agents/{AIBODY_AGENT}/controls/{cid}", {}, admin=True)
        print(f"attach control {cid} -> {s}")


def build_governed(db_path: str = ":memory:", real_memory: bool = True):
    """Wire the governed stack. real_memory=True uses the rebuilt BrainMemory core (FTS+vector+RRF,
    scan-on-write); False falls back to the toy LedgerMemory. The source memory store is never touched."""
    from memory import BrainMemory
    trace, reg, store = Trace(), Registry(), EvalStore(db_path)
    reg.register(Manifest("model", "primary"), LocalModel())
    ledger = BrainMemory(db_path) if real_memory else LedgerMemory(db_path)
    reg.register(Manifest("memory", "ledger"), ledger)
    reg.register(Manifest("tool", "status", tools=["status"]), StatusTool())
    reg.register(Manifest("evaluator", "ref-dlp"), RefEvaluator())
    reg.register(Manifest("evaluator", "agent-control"),
                 ACEvaluator(AC_BASE, AIBODY_AGENT))  # the LIVE control plane, fail-closed
    reg.register(Manifest("evaluator", "guard-model", controls={"mode": "observe"}),
                 GuardModelEvaluator(mode="observe"))  # UNCALIBRATED: warn/log only, never blocks
    heart = Heart(reg, trace, eval_store=store)
    door = LocalSurface(heart.handle, DOOR_TOKEN)
    reg.register(Manifest("surface", "local-door"), door)
    return heart, door, reg, store, trace


def _req(intent, text, **x):
    return {"token": DOOR_TOKEN, "intent": intent, "text": text, **x}


if __name__ == "__main__":
    import doctor

    print("== setup: register aibody-core agent + control on the live server ==")
    setup_ac()

    heart, door, reg, store, trace = build_governed()
    p = "taha"

    print("\n== walk through the LIVE control plane ==")
    print("benign ask     :", door.receive(_req("ask", "In one line, what is the AI Body?"), p))
    print("store benign   :", door.receive(_req("remember", "the heart is the coordinator"), p))
    print("secrets denied :", door.receive(_req("remember", "here is aws_secret_access_key=AKIA123"), p))

    print("\n== doctor (exit nonzero if the gate is not provably live) ==")
    rc = doctor.check(reg)

    print("\n== eval store (every verdict recorded) ==")
    print("counts:", store.counts())

    ok = export_otlp(trace.spans)
    print(f"\n== trace exported to OTLP collector: {ok} (fail-open if False) ==")
    print(trace.dump())
    raise SystemExit(rc)
