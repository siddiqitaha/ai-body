"""Monitoring + eval store, from line one (foundation-blueprint Phase 1).

Two sinks, both owned and local:
  - EvalStore: append-only SQLite record of every verdict the bus produced (the eval store).
  - export_otlp: best-effort push of trace spans to a local OTLP collector. TELEMETRY FAILS OPEN
    (a missing trace never blocks a request); only ENFORCEMENT fails closed.
"""
from __future__ import annotations

import json
import sqlite3
import urllib.request


class EvalStore:
    """Append-only verdict log. This is the 'eval store records verdicts' definition-of-done item."""

    def __init__(self, path: str = ":memory:") -> None:
        self.db = sqlite3.connect(path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS verdicts("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, stage TEXT, principal TEXT, evaluator TEXT, "
            "decision TEXT, confidence REAL, reason TEXT, subject_preview TEXT)")
        self.db.commit()

    def record(self, *, stage, principal, evaluator, decision, confidence, reason, subject) -> None:
        self.db.execute(
            "INSERT INTO verdicts(stage,principal,evaluator,decision,confidence,reason,subject_preview)"
            " VALUES(?,?,?,?,?,?,?)",
            (stage, principal, evaluator, decision, confidence, reason, str(subject)[:120]))
        self.db.commit()

    def all(self) -> list[dict]:
        cols = ["id", "stage", "principal", "evaluator", "decision", "confidence", "reason", "subject_preview"]
        return [dict(zip(cols, r)) for r in self.db.execute(
            "SELECT id,stage,principal,evaluator,decision,confidence,reason,subject_preview FROM verdicts")]

    def counts(self) -> dict:
        return dict(self.db.execute(
            "SELECT decision, COUNT(*) FROM verdicts GROUP BY decision").fetchall())


def export_otlp(spans: list[dict], endpoint: str = "http://127.0.0.1:4318/v1/traces",
                timeout_s: float = 2.0) -> bool:
    """Best-effort OTLP export. Returns True on success, False on any failure. NEVER raises
    (telemetry fails open). Minimal OTLP/JSON envelope; the collector on :4318 accepts it."""
    resource_spans = [{
        "resource": {"attributes": [
            {"key": "service.name", "value": {"stringValue": "ai-body"}}]},
        "scopeSpans": [{"spans": [
            {"name": s["name"], "startTimeUnixNano": str(s["t"]), "endTimeUnixNano": str(s["t"]),
             "attributes": [{"key": k, "value": {"stringValue": str(v)}}
                            for k, v in s.items() if k not in ("t", "name")]}
            for s in spans]}],
    }]
    body = json.dumps({"resourceSpans": resource_spans}).encode()
    req = urllib.request.Request(endpoint, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False  # fail OPEN: monitoring down must never block the request
