"""Mod list and mod detail routes — JSON API + legacy HTML fallback."""
from __future__ import annotations
import json
import sys
from pathlib import Path
from flask import (Blueprint, abort, current_app, jsonify, redirect,
                   render_template, request)

bp = Blueprint("mods", __name__, url_prefix="/mods")


@bp.route("/")
def mod_list():
    # React SPA uses /api/mods — redirect browsers to the SPA
    if not request.headers.get("Accept", "").startswith("application/json"):
        return redirect("/app/mods")
    scanner = current_app.config["SCANNER"]
    mods    = scanner.scan_all()
    status_filter = request.args.get("status", "all")
    search        = request.args.get("q", "").lower()
    if status_filter != "all":
        mods = [m for m in mods if m.status == status_filter]
    if search:
        mods = [m for m in mods if search in m.folder_name.lower()]
    return jsonify([m.to_dict() for m in mods])


@bp.route("/<path:mod_name>", endpoint="mod_detail")
def mod_detail(mod_name: str):
    # Skip routes with sub-paths handled by other rules
    if "/" in mod_name:
        abort(404)
    if not request.headers.get("Accept", "").startswith("application/json"):
        return redirect(f"/app/mods/{mod_name}")
    scanner = current_app.config["SCANNER"]
    mod     = scanner.get_mod(mod_name)
    if mod is None:
        return jsonify({"error": "Not found"}), 404
    return jsonify(mod.to_dict())


_STRINGS_LOAD_ALL_THRESHOLD = 5000

# Scope → key prefix(es)
_SCOPE_PREFIXES = {
    "mcm": ("mcm:",),
    "bsa": ("bsa-mcm:",),
    "swf": ("swf:",),
}


def _filter_by_scope(strings: list, scope: str) -> list:
    if scope == "all":
        return strings
    if scope == "esp":
        non_esp = ("mcm:", "bsa-mcm:", "swf:")
        return [s for s in strings if not any(s["key"].startswith(p) for p in non_esp)]
    if scope == "review":
        return [s for s in strings if s["status"] == "needs_review"]
    prefixes = _SCOPE_PREFIXES.get(scope)
    if prefixes:
        return [s for s in strings if any(s["key"].startswith(p) for p in prefixes)]
    return strings


def _scope_counts(strings: list) -> dict:
    non_esp = ("mcm:", "bsa-mcm:", "swf:")
    return {
        "all":    len(strings),
        "esp":    sum(1 for s in strings if not any(s["key"].startswith(p) for p in non_esp)),
        "mcm":    sum(1 for s in strings if s["key"].startswith("mcm:")),
        "bsa":    sum(1 for s in strings if s["key"].startswith("bsa-mcm:")),
        "swf":    sum(1 for s in strings if s["key"].startswith("swf:")),
        "review": sum(1 for s in strings if s["status"] == "needs_review"),
    }


@bp.route("/<path:mod_name>/strings")
def mod_strings(mod_name: str):
    scanner = current_app.config["SCANNER"]
    repo    = current_app.config.get("STRING_REPO")
    mod     = scanner.get_mod(mod_name)
    if mod is None:
        abort(404)

    scope         = request.args.get("scope", "all")
    filter_status = request.args.get("status", "all")
    search        = request.args.get("q", "")
    page          = int(request.args.get("page", 1))
    per_page      = int(request.args.get("per", 100))

    # JSON response for React SPA — prefer SQLite when data is available
    if request.headers.get("Accept", "").startswith("application/json"):
        if repo and repo.db.mod_row_count(mod_name) > 0:
            strings, total = repo.get_strings(
                mod_name,
                status=filter_status if filter_status != "all" else None,
                q=search or None,
                scope=scope if scope != "all" else None,
                limit=per_page,
                offset=(page - 1) * per_page,
            )
            scope_counts = repo.scope_counts(mod_name)
            # Map DB field names to frontend format
            for s in strings:
                s["esp"] = s.pop("esp_name", "")
                s.pop("mod_name", None)
                s.setdefault("dict_match", "")
            total_pages = max(1, (total + per_page - 1) // per_page)
            return jsonify({
                "strings":      strings,
                "total":        total,
                "page":         page,
                "per":          per_page,
                "pages":        total_pages,
                "scope_counts": scope_counts,
            })

        # DB empty (import still running) — fall back to scanner
        gd        = current_app.config.get("GLOBAL_DICT")
        bsa_cache = current_app.config.get("BSA_CACHE")
        swf_cache = current_app.config.get("SWF_CACHE")
        all_strings = scanner.get_mod_strings(mod_name, global_dict=gd,
                                              bsa_cache=bsa_cache, swf_cache=swf_cache)
        scope_counts = _scope_counts(all_strings)
        strings = _filter_by_scope(all_strings, scope)
        if filter_status != "all":
            strings = [s for s in strings if s["status"] == filter_status]
        if search:
            sq = search.lower()
            strings = [s for s in strings
                       if sq in s["original"].lower() or sq in s["translation"].lower()]
        total = len(strings)
        start = (page - 1) * per_page
        page_strings = strings[start:start + per_page]
        total_pages  = max(1, (total + per_page - 1) // per_page)
        return jsonify({
            "strings":      page_strings,
            "total":        total,
            "page":         page,
            "per":          per_page,
            "pages":        total_pages,
            "scope_counts": scope_counts,
        })

    # Legacy HTML response (Jinja2 fallback for non-SPA access)
    gd        = current_app.config.get("GLOBAL_DICT")
    bsa_cache = current_app.config.get("BSA_CACHE")
    swf_cache = current_app.config.get("SWF_CACHE")
    all_strings = scanner.get_mod_strings(mod_name, global_dict=gd,
                                          bsa_cache=bsa_cache, swf_cache=swf_cache)

    scope_counts   = _scope_counts(all_strings)
    total_all      = scope_counts["all"]
    over_threshold = total_all > _STRINGS_LOAD_ALL_THRESHOLD

    strings = _filter_by_scope(all_strings, scope)
    if filter_status != "all":
        strings = [s for s in strings if s["status"] == filter_status]
    if search:
        sq = search.lower()
        strings = [s for s in strings
                   if sq in s["original"].lower() or sq in s["translation"].lower()]

    total    = len(strings)
    load_all = (request.args.get("all", "").lower() in ("1", "true")
                or not over_threshold)

    if load_all:
        page_strings = strings
        per_page     = total
        total_pages  = 1
    else:
        start        = (page - 1) * per_page
        page_strings = strings[start:start + per_page]
        total_pages  = max(1, (total + per_page - 1) // per_page)

    return render_template(
        "strings.html",
        mod            = mod,
        strings        = page_strings,
        total          = total,
        total_all      = total_all,
        page           = page,
        per_page       = per_page,
        total_pages    = total_pages,
        filter_status  = filter_status,
        search         = search,
        load_all       = load_all,
        over_threshold = over_threshold,
        scope          = scope,
        scope_counts   = scope_counts,
    )


@bp.route("/<path:mod_name>/strings/update", methods=["POST"])
def update_string(mod_name: str):
    """Update a single translation in the cache (ESP) or russian txt (MCM)."""
    cfg     = current_app.config.get("TRANSLATOR_CFG")
    if cfg is None:
        return jsonify({"error": "No config"}), 500

    data     = request.get_json()
    key_str  = data.get("key")
    new_text = data.get("translation", "")

    try:
        from translator.web.workers import save_translation
        esp_name = data.get("esp", "")
        repo = current_app.config.get("STRING_REPO")
        save_translation(cfg.paths.mods_dir, mod_name,
                         cfg.paths.translation_cache,
                         esp_name, key_str, new_text, cfg=cfg, repo=repo)
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/<path:mod_name>/context")
def mod_context(mod_name: str):
    """Redirect to SPA context editor (API is at /api/mods/<name>/context)."""
    if not request.headers.get("Accept", "").startswith("application/json"):
        return redirect(f"/app/mods/{mod_name}/context")
    # Proxy to API route for JSON requests
    return redirect(f"/api/mods/{mod_name}/context")


def _load_validation(mod_name: str, app) -> dict:
    """Load saved validation results for a mod."""
    cfg = app.config.get("TRANSLATOR_CFG")
    if cfg is None:
        return {}
    try:
        result_path = cfg.paths.translation_cache.parent / f"{mod_name}_validation.json"
        if result_path.exists():
            return json.loads(result_path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _load_nexus_cache(mod, app) -> dict:
    """Load cached Nexus data for a mod (individual {mod_id}.json file).
    Accepts a ModInfo object (already fetched by caller) to avoid a second get_mod call.
    """
    cfg = app.config.get("TRANSLATOR_CFG")
    if cfg is None or mod is None:
        return {}
    try:
        if mod.nexus_mod_id:
            cache_file = cfg.paths.nexus_cache / f"{mod.nexus_mod_id}.json"
            if cache_file.exists():
                return json.loads(cache_file.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}
