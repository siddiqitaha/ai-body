"""Model routing by tier + sensitivity: the AI Body's version of cheap-first, private-by-default.

Each model row declares its tier in its manifest controls:
    controls={"tier": "local", "accepts": "any"}          # on-box, may see anything
    controls={"tier": "cloud", "accepts": "non-sensitive"} # offload, must NEVER see private data

The rule, fail-closed:
  * a SENSITIVE call may only go to a model whose `accepts` includes sensitive (i.e. a local tier);
    if none exists -> RouteDenied. Private data never leaves for a cloud tier, even to degrade.
  * a NON-sensitive call prefers a cloud tier when one is registered (offload the cheap 80%),
    else falls back to local.

Back-compat: if no model declares a tier, routing is a no-op, the single/`primary` row is returned,
so every existing single-model stack behaves exactly as before.
"""
from __future__ import annotations


class RouteDenied(Exception):
    """No model may serve this call under the policy (fail-closed)."""


def _accepts_sensitive(manifest) -> bool:
    c = manifest.controls or {}
    # explicit wins; otherwise a local tier is trusted with sensitive, a cloud tier is not
    accepts = c.get("accepts")
    if accepts:
        return accepts == "any" or accepts == "sensitive"
    return c.get("tier", "local") == "local"


def _is_cloud(manifest) -> bool:
    return (manifest.controls or {}).get("tier") == "cloud"


def choose_model(model_manifests: dict, *, sensitive: bool, default: str = "primary") -> str:
    """Return the id of the model that should serve this call. Deterministic (id-sorted tie-break)."""
    if not model_manifests:
        raise RouteDenied("no model registered")

    tiered = any((m.controls or {}).get("tier") for m in model_manifests.values())
    if not tiered:                                   # back-compat: untagged fleet -> unchanged
        return default if default in model_manifests else sorted(model_manifests)[0]

    if sensitive:
        ok = sorted(mid for mid, m in model_manifests.items() if _accepts_sensitive(m))
        if not ok:                                   # fail-closed: private data has nowhere safe to go
            raise RouteDenied("sensitive call, but no model accepts sensitive data (would leak to cloud)")
        return ok[0]

    cloud = sorted(mid for mid, m in model_manifests.items() if _is_cloud(m))
    if cloud:                                        # non-sensitive: offload to the cloud tier
        return cloud[0]
    return sorted(model_manifests)[0]                # else any model is fine
