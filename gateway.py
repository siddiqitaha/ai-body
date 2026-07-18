"""Tier 2: an out-of-process LLM gateway, the choke point the agent cannot bypass.

The heart's verdict bus is tier 1: fast, in-process, but it shares the agent's trust boundary,
so code that skips the heart skips the bus. This gateway is tier 2: a separate process that owns
the ONLY network route to the model. Every model call must cross it, and it independently consults
Agent Control (out-of-process) before forwarding. Fail-closed: deny or unreachable -> the model is
never called.

  agent ──▶ [tier 1: heart bus, in-proc] ──▶ [tier 2: THIS gateway, out-of-proc] ──▶ model :8012
                                                       │ asks Agent Control :19381
                                                       └ block -> 403, model never reached

Point the Model adapter's base at this gateway instead of :8012 and the second tier is real.
"""
from __future__ import annotations

import json
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class LLMGateway:
    """Gate (an EvaluatorPort, e.g. ACEvaluator -> the live Agent Control server) + upstream model.
    handle() gates the prompt, forwards only if allowed, then gates the response. The gate's own
    fail-closed behaviour (deny when Agent Control is unreachable) means an outage blocks, never
    leaks."""

    def __init__(self, gate, upstream: str = "http://127.0.0.1:8012/v1", timeout_s: float = 60.0):
        self.gate = gate
        self.upstream = upstream.rstrip("/")
        self.timeout_s = timeout_s
        self.forwarded = 0
        self.blocked = 0

    @staticmethod
    def _user_content(payload: dict) -> str:
        return " ".join(m.get("content", "") for m in payload.get("messages", [])
                        if m.get("role") == "user")

    def handle(self, payload: dict) -> tuple[int, dict]:
        # TIER 2, inbound: gate the prompt out-of-process BEFORE the model is reachable at all.
        v = self.gate.evaluate(self._user_content(payload), {"stage": "pre", "principal": "gateway"})
        if v.blocks:
            self.blocked += 1
            return 403, {"error": "blocked by gateway (tier 2)",
                         "decision": v.decision.value, "reason": v.reason}
        try:
            out = self._forward(payload)   # the ONLY call site that can reach the model
        except Exception as e:
            return 502, {"error": f"upstream error: {e}"}
        # TIER 2, egress: gate the model's answer on the way back out.
        try:
            answer = out["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            answer = ""
        vo = self.gate.evaluate(answer, {"stage": "post", "principal": "gateway"})
        if vo.blocks:
            self.blocked += 1
            return 403, {"error": "response blocked by gateway (tier 2)", "reason": vo.reason}
        self.forwarded += 1
        return 200, out

    def _forward(self, payload: dict) -> dict:
        body = json.dumps(payload).encode()
        req = urllib.request.Request(self.upstream + "/chat/completions", data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
            return json.loads(r.read().decode())


def make_handler(gw: LLMGateway):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                payload = {}
            status, body = gw.handle(payload)
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):  # quiet
            pass
    return Handler


def serve(gw: LLMGateway, host: str = "127.0.0.1", port: int = 19099):
    ThreadingHTTPServer((host, port), make_handler(gw)).serve_forever()


if __name__ == "__main__":
    # Live tier-2 gateway: gate = the real Agent Control server; upstream = qwen-heavy.
    import os
    from adapters import ACEvaluator
    gate = ACEvaluator("http://127.0.0.1:19381", "aibody-core")  # needs AIBODY_AC_KEY in env
    gw = LLMGateway(gate, upstream="http://127.0.0.1:8012/v1")
    port = int(os.environ.get("AIBODY_GATEWAY_PORT", "19099"))
    print(f"tier-2 LLM gateway on 127.0.0.1:{port} -> Agent Control :19381 -> model :8012")
    serve(gw, port=port)
