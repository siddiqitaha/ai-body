"""Serve the governed AI Body over HTTP: the second Surface, wired to the real stack.

The same heart, the same fail-closed gate, the same tier routing, now reachable over the network
instead of only in-process. Auth is a Bearer token; the principal comes from the X-Principal header
and is never defaulted to admin.

    python3 serve.py                      # binds 127.0.0.1:8971, token from AIBODY_HTTP_TOKEN

    curl -s localhost:8971 -H "Authorization: Bearer $AIBODY_HTTP_TOKEN" -H "X-Principal: taha" \\
         -d '{"intent":"remember","text":"the heart is the coordinator"}'
"""
from __future__ import annotations

import os

from adapters import HTTPSurface
from phase1 import build_governed


def main() -> None:
    token = os.environ.get("AIBODY_HTTP_TOKEN", "http-dev-token")
    host = os.environ.get("AIBODY_HTTP_HOST", "127.0.0.1")
    port = int(os.environ.get("AIBODY_HTTP_PORT", "8971"))

    heart, _door, _reg, _store, _trace = build_governed(with_cloud=False)
    http_door = HTTPSurface(heart.handle, token)
    srv, actual = http_door.serve(host=host, port=port)
    print(f"AI Body HTTP door on http://{host}:{actual}  (Bearer token required; principal via X-Principal)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        srv.shutdown()
        srv.server_close()


if __name__ == "__main__":
    main()
