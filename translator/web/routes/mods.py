"""Mod list and mod detail pages."""
from __future__ import annotations
import json
import sys
from pathlib import Path
from flask import (Blueprint, abort, current_app, jsonify,
                   render_template, request)

bp = Blueprint("mods", __name__, url_prefix="/mods")


@bp.route("/")
def mod_list():
    scanner = current_app.config["SCANNER"]
    mods    = scanner.scan_all()
    status_filter = request.args.get("status", "all")
    search        = request.args.get("q", "").lower()

    if status_filter != "all":
        mods = [m for m in mods if m.status == status_filter]
    if search:
        mods = [m for m in mods if search in m.folder_name.lower()]

    return render_template(
        "mods.html",
        mods          = mods,
        status_filter = status_filter,
        search        = search,
        total_count   = len(mods),
    )


@bp.route("/<path:mod_name>")
def mod_detail(mod_name: str):
    scanner = current_app.config["SCANNER"]
    jm      = current_app.config["JOB_MANAGER"]
    mod     = scanner.get_mod(mod_name)
    if mod is None:
        abort(404)

    # Recent jobs for this mod
    all_jobs = jm.list_jobs(limit=50)
    mod_jobs = [j for j in all_jobs if j.params.get("mod_name") == mod_name][:10]

    # Nexus cache data (pass already-fetched mod to avoid a second get_mod call)
    nexus_data = _load_nexus_cache(mod, current_app)

    # Validation results
    validation_data = _load_validation(mod_name, current_app)

    # Check for .trans.json files (created by translate step, separate from apply step)
    any_trans_json = any(
        Path(f.path).with_suffix('.trans.json').exists()
        for f in mod.esp_files
    )
    all_trans_json = mod.esp_files and all(
        Path(f.path).with_suffix('.trans.json').exists()
        for f in mod.esp_files
    )

    # Pre-compute pipeline step states
    pipeline_states = {
        "scan":      "done"    if mod.total_strings > 0    else "pending",
        "context":   "done"    if nexus_data               else "pending",
        "translate": "done"    if all_trans_json and mod.translated_strings > 0
                     else "partial" if any_trans_json or mod.translated_strings > 0
                     else "pending",
        "validate":  ("done" if validation_data and validation_data.get("ok")
                      else "partial" if validation_data and validation_data.get("issues_count", 0) > 0
                      else "pending"),
        "apply":     "done"    if mod.status == "done"
                     else "partial" if mod.status == "partial"
                     else "pending",
    }

    return render_template(
        "mod_detail.html",
        mod             = mod,
        mod_jobs        = mod_jobs,
        nexus_data      = nexus_data,
        pipeline_states = pipeline_states,
        validation_data = validation_data,
    )


_STRINGS_LOAD_ALL_THRESHOLD = 5000


@bp.route("/<path:mod_name>/strings")
def mod_strings(mod_name: str):
    scanner = current_app.config["SCANNER"]
    mod     = scanner.get_mod(mod_name)
    if mod is None:
        abort(404)

    filter_status = request.args.get("status", "all")
    search        = request.args.get("q", "")

    strings   = scanner.get_mod_strings(mod_name)
    total_all = len(strings)
    over_threshold = total_all > _STRINGS_LOAD_ALL_THRESHOLD

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
        page         = 1
        per_page     = total
        total_pages  = 1
    else:
        page     = int(request.args.get("page", 1))
        per_page = int(request.args.get("per", 100))
        start    = (page - 1) * per_page
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
    )


@bp.route("/<path:mod_name>/strings/update", methods=["POST"])
def update_string(mod_name: str):
    """Update a single translation in the cache."""
    cfg     = current_app.config.get("TRANSLATOR_CFG")
    if cfg is None:
        return jsonify({"error": "No config"}), 500

    data = request.get_json()
    key_str  = data.get("key")
    new_text = data.get("translation", "")

    cache_path = cfg.paths.translation_cache
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
        esp_name = data.get("esp", "").replace(".esp", "").replace(".esm", "").replace(".esl", "")
        if esp_name not in cache:
            cache[esp_name] = {}
        cache[esp_name][key_str] = new_text
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/<path:mod_name>/context")
def mod_context(mod_name: str):
    """Fetch and display Nexus context for a mod."""
    scanner = current_app.config["SCANNER"]
    cfg     = current_app.config.get("TRANSLATOR_CFG")
    mod     = scanner.get_mod(mod_name)
    if mod is None:
        abort(404)

    context_text = ""
    error        = None
    if cfg and cfg.nexus.api_key and cfg.nexus.api_key != "YOUR_NEXUS_API_KEY_HERE":
        try:
            sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
            from translator.pipeline import get_mod_context
            folder = Path(scanner.mods_dir) / mod_name
            context_text = get_mod_context(str(folder))
        except Exception as exc:
            error = str(exc)
    else:
        error = "Nexus API key not configured"

    return render_template(
        "mod_context.html",
        mod          = mod,
        context_text = context_text,
        error        = error,
    )


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
