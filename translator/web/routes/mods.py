"""Mod list and mod detail routes — JSON API + legacy HTML fallback."""
from __future__ import annotations
import json
import sys
from pathlib import Path
from flask import (Blueprint, abort, current_app, jsonify, redirect, request)
from translator.web.routes.utils import get_mod_path

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
            sort_by  = request.args.get("sort_by")  or None
            sort_dir = request.args.get("sort_dir", "asc")
            rec_type = request.args.get("rec_type") or None
            strings, total = repo.get_strings(
                mod_name,
                status=filter_status if filter_status != "all" else None,
                q=search or None,
                scope=scope if scope != "all" else None,
                rec_type=rec_type,
                sort_by=sort_by,
                sort_dir=sort_dir,
                limit=per_page,
                offset=(page - 1) * per_page,
            )
            scope_counts = repo.scope_counts(mod_name)
            # Bulk-fetch active reservations for this page's strings
            string_ids = [s["id"] for s in strings if s.get("id")]
            reserved_map: dict[int, str] = {}
            if string_ids:
                placeholders = ",".join("?" * len(string_ids))
                res_rows = repo.db.execute(
                    f"SELECT string_id, machine_label FROM string_reservations "
                    f"WHERE string_id IN ({placeholders}) AND status='active'",
                    string_ids,
                ).fetchall()
                reserved_map = {r["string_id"]: r["machine_label"] for r in res_rows}
            # Compute dup_count: how many other strings share the same original
            page_originals = list({s["original"] for s in strings if s.get("original")})
            dup_map: dict[str, int] = {}
            if page_originals:
                ph = ",".join("?" * len(page_originals))
                dup_rows = repo.db.execute(
                    f"SELECT original, COUNT(*) AS cnt FROM strings "
                    f"WHERE mod_name=? AND original IN ({ph}) GROUP BY original HAVING cnt > 1",
                    [mod_name] + page_originals,
                ).fetchall()
                dup_map = {r["original"]: r["cnt"] - 1 for r in dup_rows}
            # Map DB field names to frontend format
            for s in strings:
                s["esp"] = s.pop("esp_name", "")
                s.pop("mod_name", None)
                s.setdefault("dict_match", "")
                s["dup_count"] = dup_map.get(s.get("original", ""), 0)
                if s.get("id") in reserved_map:
                    s["reserved_by"] = reserved_map[s["id"]]
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

    # Non-JSON (browser) request → redirect to React SPA
    return redirect(f"/app/mods/{mod_name}/strings")


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
        _mp = get_mod_path(mod_name)
        computed_qs, computed_status = save_translation(
            _mp.parent if _mp else cfg.paths.mods_dir, mod_name,
            cfg.paths.translation_cache, esp_name, key_str, new_text, cfg=cfg, repo=repo)
        # Keep StatsManager in sync after manual edit
        stats_mgr = current_app.config.get("STATS_MGR")
        if stats_mgr:
            try:
                stats_mgr.invalidate(mod_name)
                stats_mgr.recompute(mod_name)
            except Exception:
                pass
        scanner = current_app.config.get("SCANNER")
        if scanner:
            scanner.invalidate(mod_name)
        return jsonify({"ok": True, "quality_score": computed_qs, "status": computed_status})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/<path:mod_name>/rec_types")
def get_rec_types(mod_name: str):
    """Return distinct record types for the record-type filter dropdown."""
    repo = current_app.config.get("STRING_REPO")
    if not repo:
        return jsonify({"rec_types": []})
    return jsonify({"rec_types": repo.get_rec_types(mod_name)})


@bp.route("/<path:mod_name>/strings/replace", methods=["POST"])
def replace_strings(mod_name: str):
    """Bulk find-and-replace in translation column."""
    repo = current_app.config.get("STRING_REPO")
    if not repo:
        return jsonify({"error": "No DB"}), 500
    data      = request.get_json() or {}
    find      = data.get("find", "")
    replace   = data.get("replace", "")
    esp_name  = data.get("esp") or None
    scope     = data.get("scope") or None
    if not find:
        return jsonify({"error": "find is required"}), 400
    count = repo.replace_in_translations(mod_name, find, replace,
                                          esp_name=esp_name, scope=scope)
    # Invalidate scanner cache so counts update
    scanner = current_app.config.get("SCANNER")
    if scanner:
        scanner.invalidate(mod_name)
    return jsonify({"ok": True, "count": count})


@bp.route("/<path:mod_name>/strings/sync-duplicates", methods=["POST"])
def sync_duplicates(mod_name: str):
    """Apply a translation to all strings with the same original text."""
    repo = current_app.config.get("STRING_REPO")
    if not repo:
        return jsonify({"error": "No DB"}), 500
    data         = request.get_json() or {}
    original     = data.get("original", "")
    translation  = data.get("translation", "")
    status       = data.get("status", "translated")
    quality_score = data.get("quality_score")
    if not original:
        return jsonify({"error": "original is required"}), 400
    count = repo.sync_duplicates(mod_name, original, translation, status, quality_score)
    return jsonify({"ok": True, "count": count})


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
