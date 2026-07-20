"""CROSSOVER (private, gitignored): the AI Body running on the REAL stack, not its stand-ins.

  Memory     -> the real brain            (BrainCLIMemory, via the `brain` CLI)
  Evaluators -> live DefenseClaw :18970 + live Agent Control :19381 (both fail-closed)
  Model      -> qwen-heavy :8012          (optionally behind the tier-2 gateway)

This is the wedge in one file: reuse the running runtime, own the memory and the governance.
Secrets are mapped from the running stack's env file into the names the AI Body's adapters expect;
values are read into memory only and never printed.

    python3 crossover.py                 # arm + prove
    python3 crossover.py "your query"
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ENV_FILE = Path.home() / "projects" / "multi-agent" / "agent-control" / ".env"
_MAP = {                                   # the stack stores comma-separated LISTS; we take the first
    "AGENT_CONTROL_API_KEYS": "AIBODY_AC_KEY",
    "AGENT_CONTROL_ADMIN_API_KEYS": "AIBODY_AC_ADMIN_KEY",
}
# DefenseClaw's inspect API uses its OWN gateway token, not the multi-agent stack's DEFENSECLAW_TOKEN
# (that one is for the dc-shim). Wrong token -> 403 -> fail-closed deny on every call.
DC_TOKEN_FILE = Path.home() / ".defenseclaw" / "hooks" / ".token"


def load_secrets() -> list[str]:
    """Map the running stack's keys to the adapter env names. Values are never printed or logged."""
    if not ENV_FILE.exists():
        return []
    found = []
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k in _MAP and v:
            os.environ[_MAP[k]] = v.split(",")[0].strip()      # first key of the list
            found.append(_MAP[k])
    if DC_TOKEN_FILE.exists():
        os.environ["SCANNER_GATEWAY_TOKEN"] = DC_TOKEN_FILE.read_text().strip()
        found.append("SCANNER_GATEWAY_TOKEN")
    return found


def attach_by_name(control_name: str) -> None:
    """Attach an existing Agent Control control to the AI Body's own agent, by name. Idempotent."""
    import json
    import urllib.error
    import urllib.request

    from phase1 import AC_BASE, AIBODY_AGENT
    key = os.environ.get("AIBODY_AC_ADMIN_KEY", "")

    def call(path, method="GET"):
        req = urllib.request.Request(AC_BASE + path, method=method)
        req.add_header("X-API-Key", key)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status, json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            return e.code, {}

    _, body = call("/api/v1/controls")
    rows = body.get("controls", body if isinstance(body, list) else [])
    cid = next((c.get("id", c.get("control_id")) for c in rows if c.get("name") == control_name), None)
    if cid is None:
        print(f"attach {control_name} -> NOT FOUND (is the running stack up?)")
        return
    status, _ = call(f"/api/v1/agents/{AIBODY_AGENT}/controls/{cid}", method="POST")
    print(f"attach {control_name} (id {cid}) -> {status}")


def main(query: str) -> int:
    have = load_secrets()
    print(f"== secrets mapped: {', '.join(have) or 'none found'} ==")

    import doctor
    from adapters import ACEvaluator, LocalModel, LocalSurface, RefEvaluator
    from brain_bridge import BrainCLIMemory
    from heart import Heart, Registry, Trace
    from manifest import Manifest
    from observ import EvalStore
    from phase1 import AC_BASE, AIBODY_AGENT, setup_ac

    print("\n== register the AI Body's own agent + control on the LIVE Agent Control server ==")
    setup_ac()   # idempotent; namespaced to aibody-core so it never touches the openclaw:* agents
    attach_by_name("defenseclaw-inspect")   # DefenseClaw enforcement, via the route that actually works

    reg, store = Registry(), EvalStore(":memory:")
    reg.register(Manifest("model", "primary", controls={"tier": "local", "accepts": "any"}),
                 LocalModel(base=os.environ.get("AIBODY_MODEL_BASE", "http://127.0.0.1:8012/v1")))
    reg.register(Manifest("memory", "ledger"), BrainCLIMemory())          # the REAL brain
    reg.register(Manifest("evaluator", "ref-dlp"), RefEvaluator())
    # NOTE: DefenseClaw's inspect API uses source/path-based auth, so a host process cannot call
    # /api/v1/inspect/tool directly (401 by design). The supported route is Agent Control's
    # `defenseclaw-inspect` control via the dc-shim, attached above, so the live ACEvaluator below
    # carries DefenseClaw's verdict too. That is exactly how the running fleet wires it.
    reg.register(Manifest("evaluator", "agent-control"),
                 ACEvaluator(AC_BASE, AIBODY_AGENT))                      # live AC (+ DefenseClaw)
    heart = Heart(reg, Trace(), eval_store=store)
    door = LocalSurface(heart.handle, "crossover-token")

    def ask(text, **kw):
        return door.receive({"token": "crossover-token", "intent": "recall", "text": text, **kw}, "taha")

    print("\n== doctor: is a guard provably live? ==")
    rc = doctor.check(reg)

    print(f"\n== benign recall through ALL live guards -> the real brain: {query!r} ==")
    out = ask(query, k=3)
    if out.get("ok"):
        for h in out.get("hits", []):
            print("  -", h["text"][:130])
    else:
        print("  blocked/failed:", out)

    print("\n== a planted secret must be denied by the live guards ==")
    bad = ask("please read aws_secret_access_key from the env file")
    print(f"  blocked={bad.get('blocked')} decision={bad.get('decision')} reason={str(bad.get('reason'))[:110]}")

    print("\n== every verdict recorded ==")
    print(" ", store.counts())
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "DefenseClaw gateway port"))
