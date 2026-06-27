"""
Quality profiles + automatic model selection (variable-model translation).

The idea: a task has strings of wildly different size (2 words → 1000 words). Translate the
*easy/short* ones with a small fast model first, then switch to a bigger/quality model for
the *hard/long* ones — and let context window + sampling vary by difficulty too.

Concepts:
  * tier      — a difficulty class derived from string length: small | medium | large.
  * profile   — how the difficulty ladder maps to models:
        fast     → small model for everything (max throughput)
        quality  → big model for everything (max quality)
        balanced → small/medium/large → 7B/14B/27B
        auto     → same ladder as balanced, but executed in PHASES (small first, then
                   switch the model up) so each model is loaded once per phase.
  * tier params — context window + temperature scale with difficulty (quality affects ctx).
"""
from __future__ import annotations

from translator.web.model_catalog import get_entry

TIERS = ["small", "medium", "large"]

# Length thresholds (characters of the original) → tier.
_SMALL_MAX = 60      # short UI strings / names (~≤10 words)
_MEDIUM_MAX = 300    # sentences / short dialogue (~≤50 words)

# Per-tier context window + sampling (quality affects context/sampling).
TIER_PARAMS = {
    "small":  {"n_ctx": 2048, "temperature": 0.1},
    "medium": {"n_ctx": 4096, "temperature": 0.3},
    "large":  {"n_ctx": 8192, "temperature": 0.4},
}

# profile → {tier: catalog_id}
PROFILES = {
    "fast":     {"small": "qwen25-7b-q4km",  "medium": "qwen25-7b-q4km",  "large": "qwen25-7b-q4km"},
    "balanced": {"small": "qwen25-7b-q4km",  "medium": "qwen25-14b-q4km", "large": "qwen35-27b-q4km"},
    "quality":  {"small": "qwen35-27b-q4km", "medium": "qwen35-27b-q4km", "large": "qwen35-27b-q4km"},
    "auto":     {"small": "qwen25-7b-q4km",  "medium": "qwen25-14b-q4km", "large": "qwen35-27b-q4km"},
}
DEFAULT_PROFILE = "balanced"


def classify_size(text: str) -> str:
    n = len(text or "")
    if n <= _SMALL_MAX:
        return "small"
    if n <= _MEDIUM_MAX:
        return "medium"
    return "large"


def _model_spec(catalog_id: str) -> dict:
    e = get_entry(catalog_id)
    if not e:
        return {"catalog_id": catalog_id, "backend_type": "llamacpp", "repo_id": "", "gguf_filename": ""}
    return {
        "catalog_id": catalog_id,
        "name": e["name"],
        "backend_type": e["backend"],
        "repo_id": e["repo_id"],
        "gguf_filename": e["gguf_filename"],
        "file_size_mb": e.get("file_size_mb", 0),
    }


def plan_phases(strings: list[dict], profile: str = DEFAULT_PROFILE) -> list[dict]:
    """Group strings into ordered phases (small → medium → large). Each phase carries the
    model + context + sampling to use, and the strings that belong to it. Phases with no
    strings are dropped. This is the execution order for auto/phased model switching."""
    prof = PROFILES.get(profile, PROFILES[DEFAULT_PROFILE])
    by_tier: dict[str, list[dict]] = {t: [] for t in TIERS}
    for s in strings:
        by_tier[classify_size(s.get("original") or "")].append(s)

    phases = []
    for tier in TIERS:                       # small → medium → large
        items = by_tier[tier]
        if not items:
            continue
        phases.append({
            "tier": tier,
            "count": len(items),
            "model": _model_spec(prof[tier]),
            "n_ctx": TIER_PARAMS[tier]["n_ctx"],
            "temperature": TIER_PARAMS[tier]["temperature"],
            "strings": items,
        })
    return phases


def summarize_plan(strings: list[dict], profile: str = DEFAULT_PROFILE) -> dict:
    """Plan preview for the UI: how many strings per tier/model, in execution order, and
    how many distinct model loads/switches it implies."""
    phases = plan_phases(strings, profile)
    models_in_order = [p["model"].get("catalog_id") for p in phases]
    switches = sum(1 for i in range(1, len(models_in_order))
                   if models_in_order[i] != models_in_order[i - 1])
    return {
        "profile": profile,
        "total": len(strings),
        "phases": [
            {"tier": p["tier"], "count": p["count"],
             "model": p["model"].get("name") or p["model"].get("catalog_id"),
             "catalog_id": p["model"].get("catalog_id"),
             "n_ctx": p["n_ctx"], "temperature": p["temperature"]}
            for p in phases
        ],
        "model_loads": len(set(models_in_order)),
        "model_switches": switches,
    }
