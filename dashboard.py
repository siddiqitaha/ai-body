"""The AI Body control room: one screen to SEE everything, flip guards on/off, attach your own
guard ("claw"), fire requests, and watch every verdict + trace, live.

    python3 dashboard.py            # demo mode, ZERO deps -> http://127.0.0.1:8971
    AIBODY_MODEL_BASE=http://127.0.0.1:11434/v1 python3 dashboard.py   # bring your own model (Ollama)

Demo mode needs nothing: if no model is reachable the Model port degrades to an echo, the guards are
the offline ones, memory is the local ledger. Everything you see is the REAL heart reflected, not a
mock, including a guard shown as "off" or "down".
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from acquire import build_toolbox
from adapters import (CoderWorker, GuardModelEvaluator, LedgerMemory, LocalModel,
                      LocalSurface, RefEvaluator, ResearcherWorker)
from heart import Heart, Registry, Trace
from manifest import Manifest
from observ import EvalStore
from ports import Decision, Verdict

HERE = Path(__file__).parent
TOKEN = os.environ.get("AIBODY_HTTP_TOKEN", "dashboard-dev-token")
APPS_FILE = HERE / "apps.json"     # private, gitignored: your real tools to cross-launch (see apps.json.example)
BOUND = {"host": "127.0.0.1", "port": 8971}   # filled at startup, used to point the test claw at ourselves

# A built-in DUMMY scanner so anyone can try the attach flow with zero setup: it speaks the same
# {content} -> {action} contract a real claw does, and blocks a few obvious test patterns.
_DUMMY_RULES = [("malware", "known-bad keyword"), ("ransom", "known-bad keyword"),
                ("<script", "script injection"), ("drop table", "sql injection"),
                ("BEGIN RSA PRIVATE KEY", "private key"), ("curl", "pipe-to-shell"),
                ("| sh", "pipe-to-shell"), ("wget", "remote fetch"), ("rm -rf", "destructive"),
                ("exfiltrate", "exfiltration"), ("ignore previous instructions", "prompt injection")]


def dummy_claw_verdict(content: str) -> dict:
    low = content.lower()
    for pat, why in _DUMMY_RULES:
        if pat.lower() in low:
            return {"action": "block", "confidence": 0.98, "reason": f"{why} ({pat!r})"}
    return {"action": "allow", "confidence": 0.0, "reason": "clean"}


def load_apps() -> list[dict]:
    """The 'cross-launch' list: your real apps (brain, DefenseClaw, ...) with a live up/down ping.
    Read from apps.json if present; nothing hardcoded, so the public repo carries no infra."""
    if not APPS_FILE.exists():
        return []
    try:
        apps = json.loads(APPS_FILE.read_text())
    except Exception:
        return []
    out = []
    for a in apps:
        url, health = a.get("url", ""), a.get("health") or a.get("url", "")
        up = False
        try:
            req = urllib.request.Request(health, method="GET")
            with urllib.request.urlopen(req, timeout=2) as r:
                up = r.status < 500
        except urllib.error.HTTPError as e:
            up = e.code < 500          # a 401/404 still means the service is answering
        except Exception:
            up = False
        out.append({"name": a.get("name", "app"), "url": url, "up": up,
                    "note": a.get("note", "")})
    return out
_UNSAFE = ("ignore previous instructions", "exfiltrate", "rm -rf", "disable logging",
           "drop table", "curl", "| sh", "wget ")


def _demo_judge(text: str) -> bool:
    t = text.lower()
    return any(m in t for m in _UNSAFE)


class HTTPClaw(RefEvaluator):
    """A guard you ATTACH at runtime: it POSTs {content} to your scanner (e.g. your DefenseClaw) and
    maps action=='block' -> DENY. Fail-closed: unreachable / bad token / error -> DENY. This is how a
    user plugs their own 'claw' into the bus without touching code."""

    def __init__(self, name: str, endpoint: str, token: str = "") -> None:
        self.name, self.endpoint, self.token = name, endpoint, token

    def evaluate(self, subject, context: dict) -> Verdict:
        body = json.dumps({"content": str(subject)}).encode()
        req = urllib.request.Request(self.endpoint, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                out = json.loads(r.read().decode() or "{}")
        except Exception as e:
            return Verdict(Decision.DENY, reason=f"{self.name} unreachable -> deny (fail-closed): {e}")
        action = str(out.get("action", out.get("raw_action", ""))).lower()
        if action == "block":
            return Verdict(Decision.DENY, confidence=float(out.get("confidence", 1.0)),
                           reason=f"{self.name}: {out.get('reason', 'blocked')}")
        return Verdict(Decision.ALLOW, reason=f"{self.name}: allow")


class Body:
    """The live governed stack + the dashboard's control over it. Guards live in a catalog; the
    registry holds only the ENABLED ones, so toggling is add/remove on the real registry."""

    def __init__(self) -> None:
        self.trace, self.store, self.reg = Trace(), EvalStore(":memory:"), Registry()
        base = os.environ.get("AIBODY_MODEL_BASE", "http://127.0.0.1:8012/v1")
        self.reg.register(Manifest("model", "primary", controls={"tier": "local", "accepts": "any"}),
                          LocalModel(base=base))
        self.reg.register(Manifest("memory", "ledger"), LedgerMemory())
        box, self.funnel = build_toolbox([RefEvaluator()])
        self.reg.register(Manifest("tool", "toolbox", tools=["status", "repo_ls", "repo_write"]), box)
        self.catalog = {                                   # everything available to switch on
            "ref-dlp": RefEvaluator(),
            "guard-model": GuardModelEvaluator(mode="observe", judge=_demo_judge),
        }
        for name in ("ref-dlp", "guard-model"):            # both on by default
            self.reg.register(Manifest("evaluator", name), self.catalog[name])
        self.reg.register(Manifest("worker", "researcher", tools=["status", "repo_ls"],
                                   memory_scope="user:you", callable_by=["heart"]), ResearcherWorker())
        self.reg.register(Manifest("worker", "coder", tools=["repo_ls", "repo_write"],
                                   memory_scope="proj:coder", callable_by=["heart"]), CoderWorker())
        self.heart = Heart(self.reg, self.trace, eval_store=self.store)
        self.door = LocalSurface(self.heart.handle, TOKEN)

    # --- control -------------------------------------------------------------
    def toggle(self, name: str, on: bool) -> bool:
        if on and name in self.catalog:
            self.reg.register(Manifest("evaluator", name), self.catalog[name])
        elif not on:
            self.reg.evaluators.pop(name, None)
            self.reg.manifests.pop(name, None)
        return name in self.reg.evaluators

    def attach_claw(self, name: str, endpoint: str, token: str) -> dict:
        if endpoint.startswith("demo"):     # a preset brick with no real endpoint -> a WORKING demo scanner
            endpoint = f"http://127.0.0.1:{BOUND['port']}/api/dummyclaw"
        claw = HTTPClaw(name, endpoint, token)
        self.catalog[name] = claw
        self.reg.register(Manifest("evaluator", name), claw)
        v = claw.evaluate("healthcheck ping", {"stage": "probe"})   # is it reachable?
        return {"attached": name, "live": not v.blocks or "unreachable" not in v.reason}

    # --- views ---------------------------------------------------------------
    def _guard_live(self, name: str, ev) -> str:
        if name not in self.reg.evaluators:
            return "off"
        if isinstance(ev, GuardModelEvaluator):
            return ev.mode                                  # observe / enforce
        if isinstance(ev, HTTPClaw):
            try:
                ev.evaluate("ping", {"stage": "probe"})
                return "live"
            except Exception:
                return "down"
        return "live"

    def state(self) -> dict:
        tools = [{"name": n, "fp": self.funnel.admitted[n].digest[:10]}
                 for n in self.reg.tools["toolbox"].list()]
        guards = [{"name": n, "on": n in self.reg.evaluators, "status": self._guard_live(n, ev)}
                  for n, ev in self.catalog.items()]
        workers = [{"id": m.id, "tools": m.tools} for m in self.reg.manifests.values()
                   if m.kind == "worker"]
        model = self.reg.models["primary"]
        return {
            "mode": "demo" if "8012" in model.base else "custom",
            "ports": {"model": model.capabilities()["id"], "memory": "ledger",
                      "tools": len(tools), "surfaces": 1, "evaluators": len(self.reg.evaluators)},
            "guards": guards, "tools": tools, "workers": workers,
            "counts": self.store.counts(),
            "model_degraded": getattr(model, "degraded", False),
        }

    def feed(self, limit: int = 40) -> list[dict]:
        return list(reversed(self.store.all()))[:limit]

    def ask(self, intent: str, text: str) -> dict:
        before = len(self.trace.spans)
        if intent.startswith("delegate:"):
            out = self.heart.delegate(intent.split(":", 1)[1], text)
        else:
            out = self.door.receive({"token": TOKEN, "intent": intent, "text": text}, "you")
        spans = [{"name": s["name"], **{k: v for k, v in s.items() if k not in ("t", "name")}}
                 for s in self.trace.spans[before:]]
        return {"result": out, "trace": spans}


BODY = Body()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            return self._send(200, (HERE / "dashboard.html").read_bytes(), "text/html; charset=utf-8")
        if self.path == "/api/state":
            return self._send(200, BODY.state())
        if self.path == "/api/feed":
            return self._send(200, BODY.feed())
        if self.path == "/api/apps":
            return self._send(200, load_apps())
        self._send(404, {"error": "not found"})

    def do_POST(self):
        b = self._read()
        if self.path == "/api/toggle":
            return self._send(200, {"name": b.get("name"), "on": BODY.toggle(b.get("name", ""), bool(b.get("on")))})
        if self.path == "/api/attach":
            return self._send(200, BODY.attach_claw(b.get("name", "claw"), b.get("endpoint", ""), b.get("token", "")))
        if self.path == "/api/ask":
            return self._send(200, BODY.ask(b.get("intent", "ask"), b.get("text", "")))
        if self.path == "/api/dummyclaw":                 # the built-in test scanner's endpoint
            return self._send(200, dummy_claw_verdict(str(b.get("content", ""))))
        if self.path == "/api/spawn-test-claw":           # one click: attach a working claw pointing at us
            url = f"http://{BOUND['host']}:{BOUND['port']}/api/dummyclaw"
            return self._send(200, BODY.attach_claw("test-claw", url, ""))
        self._send(404, {"error": "not found"})


def main():
    host = os.environ.get("AIBODY_HTTP_HOST", "127.0.0.1")
    port = int(os.environ.get("AIBODY_HTTP_PORT", "8971"))
    BOUND["host"], BOUND["port"] = "127.0.0.1", port      # the test claw calls back to us here
    srv = ThreadingHTTPServer((host, port), H)
    print(f"AI Body control room -> http://{host}:{port}   (demo mode, zero deps)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
        srv.shutdown()


if __name__ == "__main__":
    main()
