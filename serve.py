"""Serve the governed AI Body over HTTP: the second Surface, wired to the real stack.

The same heart, the same fail-closed gate, the same tier routing, now reachable over the network
instead of only in-process. Auth is a Bearer token; the principal comes from the X-Principal header
and is never defaulted to admin.

    python3 serve.py                      # binds 127.0.0.1:8971, token from AIBODY_HTTP_TOKEN

    curl -s localhost:8971 -H "Authorization: Bearer $AIBODY_HTTP_TOKEN" -H "X-Principal: taha" \\
         -d '{"intent":"remember","text":"the heart is the coordinator"}'

Defense in depth (optional): put the network door behind BOTH enforcement tiers by running the
tier-2 gateway first and pointing the model there, so every model call also crosses the
out-of-process choke point:

    python3 gateway.py &                                   # tier 2 on :19099 -> Agent Control -> model
    AIBODY_MODEL_BASE=http://127.0.0.1:19099/v1 \\
    AIBODY_GUARD_MODE=enforce python3 serve.py             # tier 1 bus in enforce + tier 2 gateway

Env: AIBODY_HTTP_TOKEN, AIBODY_HTTP_HOST, AIBODY_HTTP_PORT, AIBODY_MODEL_BASE (route the model
through the gateway), AIBODY_GUARD_MODE (observe|enforce; enforce needs the calibration receipt).
"""
from __future__ import annotations

import os

from adapters import HTTPSurface
from phase1 import build_governed


def main() -> None:
    token = os.environ.get("AIBODY_HTTP_TOKEN", "http-dev-token")
    host = os.environ.get("AIBODY_HTTP_HOST", "127.0.0.1")
    port = int(os.environ.get("AIBODY_HTTP_PORT", "8971"))
    model_base = os.environ.get("AIBODY_MODEL_BASE")            # e.g. the tier-2 gateway
    guard_mode = os.environ.get("AIBODY_GUARD_MODE", "observe")

    heart, _door, _reg, _store, _trace = build_governed(
        with_cloud=False, model_base=model_base, guard_mode=guard_mode)
    http_door = HTTPSurface(heart.handle, token)
    srv, actual = http_door.serve(host=host, port=port)
    tiers = "tier-1 bus" + (f" ({guard_mode})") + (" + tier-2 gateway" if model_base else "")
    print(f"AI Body HTTP door on http://{host}:{actual}  [{tiers}]  (Bearer token; principal via X-Principal)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        srv.shutdown()
        srv.server_close()


if __name__ == "__main__":
    main()
