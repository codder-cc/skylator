"""Tools page — ESP parse/dump, BSA pack/unpack, SWF decompile/compile, xTranslate."""
from __future__ import annotations
import json
import subprocess
import tempfile
from pathlib import Path
from flask import (Blueprint, current_app, jsonify,
                   redirect, request)

bp = Blueprint("tools", __name__, url_prefix="/tools")


@bp.route("/")
def tools_page():
    if not request.headers.get("Accept", "").startswith("application/json"):
        return redirect("/app/tools")
    return jsonify({"ok": True})


# ── ESP Tools ────────────────────────────────────────────────────────────────

@bp.route("/esp/parse", methods=["POST"])
def esp_parse():
    """Parse an ESP file and return extracted strings as JSON."""
    data     = request.get_json() or {}
    esp_path = data.get("path", "")
    if not esp_path:
        return jsonify({"error": "No path"}), 400

    p = Path(esp_path)
    if not p.exists():
        return jsonify({"error": "File not found"}), 404

    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
        from scripts.esp_engine import EspParser
        parser   = EspParser(p)
        extracted = parser.extract_strings()
        strings = [{"key": str(k), "text": v} for k, v in extracted.items()]
        return jsonify({"count": len(strings), "strings": strings[:200]})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/esp/validate", methods=["POST"])
def esp_validate():
    """Validate translated ESP strings for token preservation, encoding, etc."""
    cfg  = current_app.config.get("TRANSLATOR_CFG")
    data = request.get_json() or {}
    mod_name = data.get("mod_name", "")

    jm = current_app.config["JOB_MANAGER"]
    from translator.web.workers import validate_translations_worker

    def run(job):
        validate_translations_worker(job, cfg, mod_name)

    job = jm.create(
        name     = f"Validate: {mod_name}",
        job_type = "validate",
        params   = {"mod_name": mod_name},
        fn       = run,
    )
    return jsonify({"job_id": job.id})


@bp.route("/esp/apply", methods=["POST"])
def esp_apply():
    """Apply translation cache to ESP — write translated strings into binary."""
    cfg  = current_app.config.get("TRANSLATOR_CFG")
    data = request.get_json() or {}
    esp_path = data.get("esp_path", "")
    dry_run  = data.get("dry_run", False)

    if not esp_path:
        return jsonify({"error": "No esp_path"}), 400

    jm = current_app.config["JOB_MANAGER"]
    from translator.web.workers import translate_esp_worker

    def run(job):
        translate_esp_worker(job, cfg, esp_path, dry_run=dry_run)

    job = jm.create(
        name     = f"Apply ESP: {Path(esp_path).name}",
        job_type = "translate_esp",
        params   = {"esp_path": esp_path},
        fn       = run,
    )
    return jsonify({"job_id": job.id})


# ── BSA Tools ────────────────────────────────────────────────────────────────

@bp.route("/bsa/unpack", methods=["POST"])
def bsa_unpack():
    cfg  = current_app.config.get("TRANSLATOR_CFG")
    if cfg is None:
        return jsonify({"error": "No config"}), 500

    data     = request.get_json() or {}
    bsa_path = data.get("bsa_path", "")
    out_dir  = data.get("out_dir", "")

    if not bsa_path:
        return jsonify({"error": "No bsa_path"}), 400
    if not out_dir:
        out_dir = str(Path(bsa_path).parent / (Path(bsa_path).stem + "_unpacked"))

    jm = current_app.config["JOB_MANAGER"]
    from translator.web.workers import bsa_unpack_worker

    def run(job):
        bsa_unpack_worker(job, cfg, bsa_path, out_dir)

    job = jm.create(
        name     = f"BSA Unpack: {Path(bsa_path).name}",
        job_type = "bsa_unpack",
        params   = {"bsa_path": bsa_path, "out_dir": out_dir},
        fn       = run,
    )
    return jsonify({"job_id": job.id})


@bp.route("/bsa/pack", methods=["POST"])
def bsa_pack():
    cfg  = current_app.config.get("TRANSLATOR_CFG")
    if cfg is None:
        return jsonify({"error": "No config"}), 500

    data    = request.get_json() or {}
    src_dir = data.get("src_dir", "")
    bsa_out = data.get("bsa_path", "")

    if not src_dir or not bsa_out:
        return jsonify({"error": "src_dir and bsa_path required"}), 400

    jm = current_app.config["JOB_MANAGER"]
    from translator.web.workers import bsa_pack_worker

    def run(job):
        bsa_pack_worker(job, cfg, src_dir, bsa_out)

    job = jm.create(
        name     = f"BSA Pack: {Path(bsa_out).name}",
        job_type = "bsa_pack",
        params   = {"src_dir": src_dir, "bsa_path": bsa_out},
        fn       = run,
    )
    return jsonify({"job_id": job.id})


# ── SWF Tools (JPEXS FFDec) ─────────────────────────────────────────────────

@bp.route("/swf/decompile", methods=["POST"])
def swf_decompile():
    data     = request.get_json() or {}
    swf_path = data.get("swf_path", "")
    ffdec    = data.get("ffdec_jar", "")
    out_dir  = data.get("out_dir", "")

    if not swf_path or not ffdec:
        return jsonify({"error": "swf_path and ffdec_jar required"}), 400
    if not out_dir:
        out_dir = str(Path(swf_path).parent / (Path(swf_path).stem + "_decompiled"))

    jm = current_app.config["JOB_MANAGER"]
    from translator.web.workers import swf_decompile_worker

    def run(job):
        swf_decompile_worker(job, ffdec, swf_path, out_dir)

    job = jm.create(
        name     = f"SWF Decompile: {Path(swf_path).name}",
        job_type = "swf_decompile",
        params   = {"swf_path": swf_path, "out_dir": out_dir},
        fn       = run,
    )
    return jsonify({"job_id": job.id})


@bp.route("/swf/compile", methods=["POST"])
def swf_compile():
    data     = request.get_json() or {}
    src_dir  = data.get("src_dir", "")
    swf_path = data.get("swf_path", "")
    ffdec    = data.get("ffdec_jar", "")

    if not src_dir or not swf_path or not ffdec:
        return jsonify({"error": "src_dir, swf_path, ffdec_jar required"}), 400

    jm = current_app.config["JOB_MANAGER"]
    from translator.web.workers import swf_compile_worker

    def run(job):
        swf_compile_worker(job, ffdec, src_dir, swf_path)

    job = jm.create(
        name     = f"SWF Compile: {Path(swf_path).name}",
        job_type = "swf_compile",
        params   = {"src_dir": src_dir, "swf_path": swf_path},
        fn       = run,
    )
    return jsonify({"job_id": job.id})


# ── Hash Tools ───────────────────────────────────────────────────────────────

@bp.route("/hashes")
def hash_manager():
    if not request.headers.get("Accept", "").startswith("application/json"):
        return redirect("/app/tools")
    cfg     = current_app.config.get("TRANSLATOR_CFG")
    scanner = current_app.config["SCANNER"]
    hashes  = _build_hash_list(cfg, scanner)
    return jsonify({"hashes": hashes})


@bp.route("/hashes/compute", methods=["POST"])
def compute_hashes():
    """Compute hashes for all mod ESP/BSA files and return as JSON."""
    cfg     = current_app.config.get("TRANSLATOR_CFG")
    scanner = current_app.config["SCANNER"]
    hashes  = _build_hash_list(cfg, scanner)
    return jsonify(hashes)


def _build_hash_list(cfg, scanner) -> list[dict]:
    if cfg is None:
        return []
    mods = scanner.scan_all()
    result = []
    for mod in mods:
        for f in mod.esp_files + mod.bsa_files:
            p = Path(f.path)
            if not p.stem:          # skip ghost files like ".esp"
                continue
            try:
                result.append({
                    "mod":    mod.folder_name,
                    "file":   f.name,
                    "path":   f.path,
                    "ext":    f.ext,
                    "size":   f.size_bytes,
                    "hash":   scanner.file_hash(p),
                })
            except Exception:
                pass
    return result


# ── xTranslate / xEdit Info ──────────────────────────────────────────────────

@bp.route("/xtranslate/import", methods=["POST"])
def xtranslate_import():
    """
    Import translations from an SST/xTranslate .t3dict file.
    SST format (text-based):
        [FormID]
        FULL=Translated name
        DESC=Translated description
    Also accepts our XML export format for round-trip.
    """
    data      = request.get_json() or {}
    t3dict    = data.get("t3dict_path", "")
    cfg       = current_app.config.get("TRANSLATOR_CFG")

    if not t3dict or not cfg:
        return jsonify({"error": "t3dict_path required + config needed"}), 400

    p = Path(t3dict)
    if not p.exists():
        return jsonify({"error": "File not found"}), 404

    try:
        content = p.read_text(encoding="utf-8", errors="replace")
        imported: dict[str, dict] = {}
        count = 0

        if content.lstrip().startswith("<"):
            # XML fallback (our own export format)
            import xml.etree.ElementTree as ET
            root = ET.fromstring(content)
            for dict_el in root.findall(".//Dict"):
                mod = dict_el.get("Mod", "unknown")
                imported.setdefault(mod, {})
                for s in dict_el.findall("String"):
                    src  = s.get("Source", "")
                    tran = s.get("Translation", "")
                    if src and tran:
                        imported[mod][src] = tran
                        count += 1
        else:
            # SST text format: [PluginName.esp]\n[FormID]\nFIELD=Text
            current_plugin = "unknown"
            current_fid    = None
            for line in content.splitlines():
                line = line.rstrip()
                if not line:
                    continue
                if line.startswith("[") and line.endswith("]"):
                    inner = line[1:-1]
                    if "." in inner and inner.lower().endswith((".esp", ".esm", ".esl")):
                        current_plugin = inner.rsplit(".", 1)[0]
                        imported.setdefault(current_plugin, {})
                    else:
                        current_fid = inner  # FormID section
                elif "=" in line and current_fid:
                    field, _, text = line.partition("=")
                    key = f"{current_fid}_{field.strip()}"
                    imported[current_plugin][key] = text
                    count += 1

        # Merge into translation cache
        cache_path = cfg.paths.translation_cache
        cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
        for mod, strings in imported.items():
            cache.setdefault(mod, {}).update(strings)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")

        return jsonify({"ok": True, "imported": count})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/xtranslate/export", methods=["POST"])
def xtranslate_export():
    """
    Export current translation cache as SST/xTranslate-compatible .t3dict text file.
    Format:
        [PluginName.esp]
        [FormID]
        FIELD=Translation
    """
    data     = request.get_json() or {}
    out_path = data.get("out_path", "")
    fmt      = data.get("format", "sst")   # "sst" (default) or "xml"
    cfg      = current_app.config.get("TRANSLATOR_CFG")

    if not cfg:
        return jsonify({"error": "No config"}), 500

    cache_path = cfg.paths.translation_cache
    if not cache_path.exists():
        return jsonify({"error": "No translation cache"}), 404

    cache = json.loads(cache_path.read_text(encoding="utf-8"))

    if fmt == "xml":
        # XML format for our own round-trips
        lines = ['<?xml version="1.0" encoding="utf-8"?>', "<Dicts>"]
        for mod, strings in cache.items():
            lines.append(f'  <Dict Mod="{mod}">')
            for key, tran in strings.items():
                k = str(key).replace("&", "&amp;").replace('"', "&quot;")
                t = str(tran).replace("&", "&amp;").replace('"', "&quot;")
                lines.append(f'    <String Source="{k}" Translation="{t}"/>')
            lines.append("  </Dict>")
        lines.append("</Dicts>")
        content = "\n".join(lines)
        mime    = "application/xml"
        fname   = "translations.xml"
    else:
        # SST text format
        lines = []
        for mod, strings in cache.items():
            lines.append(f"[{mod}.esp]")
            # Group by fake FormID (key prefix)
            for key, tran in strings.items():
                # key is like "('0001A2B3', 'NPC_', 'FULL', 2)"
                # extract field if possible
                parts = str(key).strip("()").split(",")
                fid   = parts[0].strip().strip("'") if parts else "0"
                field = parts[2].strip().strip("'") if len(parts) > 2 else "TEXT"
                lines.append(f"[{fid}]")
                lines.append(f"{field}={tran}")
            lines.append("")
        content = "\n".join(lines)
        mime    = "text/plain"
        fname   = "translations.t3dict"

    if out_path:
        Path(out_path).write_text(content, encoding="utf-8")
        return jsonify({"ok": True, "path": out_path})
    else:
        return current_app.response_class(
            content,
            mimetype=mime,
            headers={"Content-Disposition": f"attachment; filename={fname}"},
        )


@bp.route("/nexus/fetch", methods=["POST"])
def nexus_fetch():
    """Manually fetch Nexus description for a mod."""
    data     = request.get_json() or {}
    mod_name = data.get("mod_name", "")
    cfg      = current_app.config.get("TRANSLATOR_CFG")
    scanner  = current_app.config["SCANNER"]

    if not cfg:
        return jsonify({"error": "No config"}), 500

    mod = scanner.get_mod(mod_name)
    if mod is None:
        return jsonify({"error": "Mod not found"}), 404

    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
        from translator.context.nexus_fetcher import NexusFetcher
        fetcher  = NexusFetcher()
        folder   = cfg.paths.mods_dir / mod_name
        desc     = fetcher.fetch_mod_description(folder)
        # Also return the full cached record so the UI can update without a reload
        mod      = scanner.get_mod(mod_name)
        cached   = {}
        if mod and mod.nexus_mod_id:
            import json as _json
            cache_file = cfg.paths.nexus_cache / f"{mod.nexus_mod_id}.json"
            if cache_file.exists():
                cached = _json.loads(cache_file.read_text(encoding="utf-8"))
        return jsonify({"ok": True, "description": desc, "data": cached})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
