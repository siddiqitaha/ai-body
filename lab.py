"""Governed Agents Lab — point it at your box, it discovers your real stack and shows it.

    python3 lab.py                 # http://127.0.0.1:8972

It probes what's actually running (no fakes): DefenseClaw, Agent Control, the model, and the local
Splunk that DefenseClaw already ships. Anything governed is discoverable, so the box fills with your
real components and the setup guide checks itself off as pieces come up.

Env (all optional, sane local defaults):
  DC_HEALTH, AC_BASE, AC_KEY, MODEL_BASE, SPLUNK_WEB, OPENCLAW_BASE, GALILEO_BASE
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).parent
DC_CONFIG = Path.home() / ".defenseclaw" / "config.yaml"

CFG = {
    "dc_health": os.environ.get("DC_HEALTH", "http://127.0.0.1:18970/health"),
    "ac_base": os.environ.get("AC_BASE", "http://127.0.0.1:19381"),
    "ac_key": os.environ.get("AC_KEY", os.environ.get("AIBODY_AC_KEY", "")),
    "model_base": os.environ.get("MODEL_BASE", "http://127.0.0.1:8012/v1"),
    "splunk_web": os.environ.get("SPLUNK_WEB", "http://127.0.0.1:8090/"),
    "openclaw_base": os.environ.get("OPENCLAW_BASE", ""),   # e.g. http://127.0.0.1:19289
    "galileo_base": os.environ.get("GALILEO_BASE", ""),
}


def _probe(url: str, timeout: float = 3.0, key: str | None = None):
    """Return (up, status, body_text|None). up=True if the service answers at all (even 401/404)."""
    if not url:
        return (False, 0, None)
    try:
        req = urllib.request.Request(url, method="GET")
        if key:
            req.add_header("X-API-Key", key)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return (True, r.status, r.read(20000).decode(errors="replace"))
    except urllib.error.HTTPError as e:
        return (True, e.code, None)          # answering, just not 2xx (auth/paths) -> service is UP
    except Exception:
        return (False, 0, None)


def _dc_config() -> dict:
    """Read DefenseClaw's config for its Splunk output + guardrail mode (keys only, never secrets)."""
    out = {"splunk": None, "mode": None, "connectors": []}
    if not DC_CONFIG.exists():
        return out
    txt = DC_CONFIG.read_text(errors="replace")
    if re.search(r"kind:\s*splunk_hec", txt):
        out["splunk"] = "splunk_hec (from DefenseClaw config)"
    m = re.search(r"mode:\s*(observe|action|monitor)", txt)
    if m:
        out["mode"] = m.group(1)
    out["connectors"] = re.findall(r"(codex|claudecode|openclaw|antigravity)", txt)
    return out


def _agents() -> list[dict]:
    """Discover governed agents from Agent Control (authoritative), else OpenClaw's model list."""
    agents = []
    up, _, body = _probe(CFG["ac_base"] + "/api/v1/agents", key=CFG["ac_key"] or None)
    if up and body:
        try:
            rows = json.loads(body)
            rows = rows.get("agents", rows) if isinstance(rows, dict) else rows
            for a in rows:
                name = a.get("agent_name") or a.get("name") or a.get("id")
                if name:
                    agents.append({"name": name, "governed": True, "src": "Agent Control"})
        except Exception:
            pass
    if not agents and CFG["openclaw_base"]:
        up, _, body = _probe(CFG["openclaw_base"] + "/v1/models")
        if up and body:
            try:
                for m in json.loads(body).get("data", []):
                    mid = str(m.get("id", ""))
                    if mid.startswith("openclaw/"):
                        agents.append({"name": mid.split("/", 1)[1], "governed": True, "src": "OpenClaw"})
            except Exception:
                pass
    return agents


def discover() -> dict:
    dc = _dc_config()
    dc_up = _probe(CFG["dc_health"])[0]
    ac_up = _probe(CFG["ac_base"] + "/health")[0]
    model_up, _, model_body = _probe(CFG["model_base"] + "/models")
    splunk_up = _probe(CFG["splunk_web"], timeout=3)[0]
    oc_up = _probe(CFG["openclaw_base"] + "/v1/models")[0] if CFG["openclaw_base"] else False
    agents = _agents()

    model_name = "local model"
    if model_body:
        try:
            model_name = json.loads(model_body)["data"][0]["id"]
        except Exception:
            pass

    comps = [
        {"id": "defenseclaw", "kind": "guard", "name": "DefenseClaw", "up": dc_up,
         "detail": f"scanner · guardrail {dc['mode'] or '?'}", "action": {"label": "TUI", "type": "tui"}},
        {"id": "agent-control", "kind": "guard", "name": "Agent Control", "up": ac_up,
         "detail": "policy engine", "action": {"label": "open UI", "url": CFG["ac_base"] + "/"}},
        {"id": "openclaw", "kind": "runtime", "name": "OpenClaw", "up": oc_up,
         "detail": "agent runtime", "action": None},
        {"id": "model", "kind": "model", "name": model_name, "up": model_up, "detail": "the LLM", "action": None},
        {"id": "splunk", "kind": "audit", "name": "Splunk", "up": splunk_up,
         "detail": dc["splunk"] or "audit", "action": {"label": "open Splunk", "url": CFG["splunk_web"]}},
        {"id": "galileo", "kind": "audit", "name": "Galileo", "up": bool(CFG["galileo_base"]),
         "detail": "traces (connect a token)", "action": None},
    ]
    checklist = [
        {"step": "DefenseClaw running", "done": dc_up, "how": "systemctl --user start defenseclaw-gateway"},
        {"step": "Local model up", "done": model_up, "how": "start your model server"},
        {"step": "Governance stack up (Agent Control + OpenClaw)", "done": ac_up and oc_up,
         "how": "cd ~/projects/multi-agent && ./up.sh"},
        {"step": "Agents discovered", "done": bool(agents), "how": "up.sh registers them; then they appear in the box"},
        {"step": "Splunk audit reachable", "done": splunk_up, "how": "ships with DefenseClaw (local Splunk)"},
        {"step": "Galileo connected", "done": bool(CFG["galileo_base"]),
         "how": "set GALILEO_BASE + token to stream traces"},
    ]
    return {"components": comps, "agents": agents, "checklist": checklist,
            "ready": sum(1 for c in checklist if c["done"]), "total": len(checklist),
            "dc": dc}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            return self._send(200, (HERE / "lab.html").read_bytes(), "text/html; charset=utf-8")
        if self.path == "/api/discover":
            return self._send(200, discover())
        self._send(404, {"error": "not found"})


def main():
    port = int(os.environ.get("LAB_PORT", "8972"))
    print(f"Governed Agents Lab -> http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()


if __name__ == "__main__":
    main()
