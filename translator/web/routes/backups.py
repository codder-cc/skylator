"""Backup / restore management."""
from __future__ import annotations
import json
import logging
import os
import shutil
import time
from pathlib import Path
from flask import (Blueprint, abort, current_app, jsonify,
                   redirect, request)

log = logging.getLogger(__name__)

bp = Blueprint("backups", __name__, url_prefix="/backups")


@bp.route("/")
def backup_list():
    if not request.headers.get("Accept", "").startswith("application/json"):
        return redirect("/app/backups")
    return jsonify(_list_backups(current_app))


@bp.route("/list")
def backup_list_json():
    """JSON version of backup list for React SPA."""
    return jsonify({"backups": _list_backups(current_app)})


@bp.route("/create", methods=["POST"])
def create_backup():
    """Create a backup of mod files."""
    cfg = current_app.config.get("TRANSLATOR_CFG")
    if cfg is None:
        return jsonify({"error": "No config"}), 500

    data     = request.get_json() or {}
    mod_name = data.get("mod_name")
    label    = data.get("label", "manual")

    backup_dir = cfg.paths.backup_dir
    backup_dir.mkdir(parents=True, exist_ok=True)

    ts = time.strftime("%Y%m%d_%H%M%S")
    if mod_name:
        src  = cfg.paths.mods_dir / mod_name
        dest = backup_dir / f"{mod_name}__{ts}__{label}"
        if not src.is_dir():
            return jsonify({"error": "Mod not found"}), 404
        shutil.copytree(str(src), str(dest))
    else:
        # Full backup of translation cache
        src  = cfg.paths.translation_cache
        dest = backup_dir / f"translation_cache__{ts}__{label}.json"
        if src.exists():
            shutil.copy2(str(src), str(dest))
        else:
            return jsonify({"error": "No translation cache to back up"}), 404

    return jsonify({"ok": True, "path": str(dest)})


@bp.route("/<path:backup_id>/restore", methods=["POST"])
def restore_backup(backup_id: str):
    """Restore a mod backup to its original location."""
    cfg = current_app.config.get("TRANSLATOR_CFG")
    if cfg is None:
        return jsonify({"error": "No config"}), 500

    backup_path = cfg.paths.backup_dir / backup_id
    if not backup_path.exists():
        return jsonify({"error": "Backup not found"}), 404

    # Parse mod name from backup folder name: ModName__timestamp__label
    parts = backup_id.split("__")
    if len(parts) < 2:
        return jsonify({"error": "Cannot determine original mod name"}), 400

    mod_name = parts[0]

    # JSON file backup → restore translation cache
    if str(backup_path).endswith(".json"):
        dest = cfg.paths.translation_cache
        shutil.copy2(str(backup_path), str(dest))
        return jsonify({"ok": True, "restored_to": str(dest)})

    # Directory backup → restore mod folder
    dest = cfg.paths.mods_dir / mod_name
    if dest.exists():
        # Keep current as safety backup
        safety = cfg.paths.backup_dir / f"{mod_name}__before_restore__{int(time.time())}"
        shutil.copytree(str(dest), str(safety))
        shutil.rmtree(str(dest))
    shutil.copytree(str(backup_path), str(dest))
    return jsonify({"ok": True, "restored_to": str(dest)})


@bp.route("/<path:backup_id>/delete", methods=["POST"])
def delete_backup(backup_id: str):
    cfg = current_app.config.get("TRANSLATOR_CFG")
    if cfg is None:
        return jsonify({"error": "No config"}), 500

    backup_path = cfg.paths.backup_dir / backup_id
    if not backup_path.exists():
        return jsonify({"error": "Not found"}), 404

    if backup_path.is_dir():
        shutil.rmtree(str(backup_path))
    else:
        backup_path.unlink()
    return jsonify({"ok": True})


@bp.route("/restore-mod-esp", methods=["POST"])
def restore_mod_esp():
    """Restore all translatable file backups (ESP, ESM, BSA, SWF) for a mod and clear caches."""
    cfg = current_app.config.get("TRANSLATOR_CFG")
    if cfg is None:
        return jsonify({"error": "No config"}), 500

    data = request.get_json() or {}
    mod_name = data.get("mod_name")
    if not mod_name:
        return jsonify({"error": "mod_name is required"}), 400

    mod_backup_dir = cfg.paths.backup_dir / mod_name
    if not mod_backup_dir.is_dir():
        return jsonify({"error": f"No backups found for mod '{mod_name}'"}), 404

    # Collect all translatable file types backed up under backup_dir / mod_name /
    EXTS = ("*.esp", "*.esm", "*.bsa", "*.swf")
    backup_files = []
    for pat in EXTS:
        backup_files.extend(mod_backup_dir.rglob(pat))

    if not backup_files:
        return jsonify({"error": f"No backups found for mod '{mod_name}'"}), 404

    restored: list[str] = []

    for backup_path in backup_files:
        rel = backup_path.relative_to(cfg.paths.backup_dir)
        original_path = cfg.paths.mods_dir / rel
        original_path.parent.mkdir(parents=True, exist_ok=True)
        log.info("Restoring %s: %s -> %s", backup_path.suffix, backup_path, original_path)
        shutil.copy2(str(backup_path), str(original_path))

        # Remove companion .trans.json for ESP/ESM
        # (.with_suffix replaces the last extension, so ACatsLife.esp → ACatsLife.trans.json)
        if backup_path.suffix in (".esp", ".esm"):
            trans_json = original_path.with_suffix(".trans.json")
            if trans_json.exists():
                log.info("Removing trans.json: %s", trans_json)
                trans_json.unlink()

        restored.append(backup_path.name)

    esp_files = [f for f in backup_files if f.suffix in (".esp", ".esm")]

    # --- Clear translation_cache.json entries for this mod ---
    cache_path = cfg.paths.translation_cache
    if cache_path.exists() and esp_files:
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
            stems_to_remove = {f.stem.lower() for f in esp_files}
            keys_removed = [k for k in list(cache.keys()) if k.lower() in stems_to_remove]
            for k in keys_removed:
                del cache[k]
            if keys_removed:
                cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
                log.info("Removed %d entries from translation_cache.json for mod '%s'", len(keys_removed), mod_name)
        except Exception as exc:
            log.warning("Could not update translation_cache.json: %s", exc)

    # --- Clear _string_counts.json entries for this mod ---
    string_counts_path = cfg.paths.backup_dir.parent / "_string_counts.json"
    if not string_counts_path.exists():
        string_counts_path = Path(cfg.paths.translation_cache).parent / "_string_counts.json"
    if string_counts_path.exists():
        try:
            counts = json.loads(string_counts_path.read_text(encoding="utf-8"))
            keys_to_remove = [k for k in list(counts.keys()) if k.startswith(f"{mod_name}/")]
            for k in keys_to_remove:
                del counts[k]
            if keys_to_remove:
                string_counts_path.write_text(json.dumps(counts, ensure_ascii=False, indent=2), encoding="utf-8")
                log.info("Removed %d entries from _string_counts.json for mod '%s'", len(keys_to_remove), mod_name)
        except Exception as exc:
            log.warning("Could not update _string_counts.json: %s", exc)

    return jsonify({"ok": True, "restored": restored})


@bp.route("/trans-json/snapshot", methods=["POST"])
def snapshot_trans_json():
    """Save a lightweight snapshot of a mod's .trans.json files (just their current state).

    This is called automatically before a translation job modifies strings.
    Body: { "mod_name": "SomeMod" }
    """
    cfg = current_app.config.get("TRANSLATOR_CFG")
    if cfg is None:
        return jsonify({"error": "No config"}), 500

    data = request.get_json() or {}
    mod_name = data.get("mod_name")
    if not mod_name:
        return jsonify({"error": "mod_name required"}), 400

    mod_dir = cfg.paths.mods_dir / mod_name
    if not mod_dir.is_dir():
        return jsonify({"error": "Mod not found"}), 404

    backup_dir = cfg.paths.backup_dir
    backup_dir.mkdir(parents=True, exist_ok=True)

    ts = time.strftime("%Y%m%d_%H%M%S")
    snap_dir = backup_dir / f"{mod_name}__trans_{ts}__auto-snap"
    snap_dir.mkdir(parents=True, exist_ok=True)

    saved = []
    for trans_json in mod_dir.rglob("*.trans.json"):
        rel = trans_json.relative_to(mod_dir)
        dest = snap_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(trans_json), str(dest))
        saved.append(str(rel))

    if not saved:
        # Nothing to snapshot — remove empty dir
        snap_dir.rmdir()
        return jsonify({"ok": True, "saved": [], "note": "no trans.json files found"})

    return jsonify({"ok": True, "saved": saved, "snapshot": snap_dir.name})


@bp.route("/trans-json/list", methods=["GET"])
def list_trans_snapshots():
    """List all .trans.json snapshots for a mod."""
    cfg = current_app.config.get("TRANSLATOR_CFG")
    if cfg is None:
        return jsonify({"error": "No config"}), 500

    mod_name = request.args.get("mod_name", "")
    backup_dir = cfg.paths.backup_dir
    if not backup_dir.is_dir():
        return jsonify({"snapshots": []})

    snapshots = []
    for p in sorted(backup_dir.iterdir(), reverse=True):
        if not p.is_dir():
            continue
        if mod_name and not p.name.startswith(f"{mod_name}__trans_"):
            continue
        if "__trans_" not in p.name:
            continue
        parts = p.name.split("__")
        snap_mod = parts[0] if parts else p.name
        ts_str = parts[1].replace("trans_", "") if len(parts) > 1 else ""
        label = parts[2] if len(parts) > 2 else ""
        size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
        snapshots.append({
            "id": p.name,
            "mod_name": snap_mod,
            "ts_str": ts_str,
            "label": label,
            "size": size,
            "files": [str(f.relative_to(p)) for f in p.rglob("*.trans.json")],
        })

    return jsonify({"snapshots": snapshots})


@bp.route("/checkpoints", methods=["GET"])
def list_checkpoints():
    mod_name = request.args.get("mod_name")
    repo = current_app.config.get("STRING_REPO")
    if repo is None:
        return jsonify({"checkpoints": []})
    return jsonify({"checkpoints": repo.list_checkpoints(mod_name or None)})


@bp.route("/checkpoints/create", methods=["POST"])
def create_checkpoint():
    data = request.get_json() or {}
    mod_name = data.get("mod_name")
    esp_name = data.get("esp_name")
    if not mod_name:
        return jsonify({"error": "mod_name required"}), 400
    repo = current_app.config.get("STRING_REPO")
    if repo is None:
        return jsonify({"error": "DB not available"}), 500
    checkpoint_id = repo.create_checkpoint(mod_name, esp_name)
    return jsonify({"ok": True, "checkpoint_id": checkpoint_id})


@bp.route("/checkpoints/<checkpoint_id>/restore", methods=["POST"])
def restore_checkpoint(checkpoint_id: str):
    repo = current_app.config.get("STRING_REPO")
    if repo is None:
        return jsonify({"error": "DB not available"}), 500
    n = repo.restore_checkpoint(checkpoint_id)
    return jsonify({"ok": True, "restored": n})


@bp.route("/checkpoints/<checkpoint_id>/delete", methods=["POST"])
def delete_checkpoint(checkpoint_id: str):
    repo = current_app.config.get("STRING_REPO")
    if repo is None:
        return jsonify({"error": "DB not available"}), 500
    repo.delete_checkpoint(checkpoint_id)
    return jsonify({"ok": True})


def _list_backups(app) -> list[dict]:
    cfg = app.config.get("TRANSLATOR_CFG")
    if cfg is None:
        return []

    backup_dir = cfg.paths.backup_dir
    if not backup_dir.is_dir():
        return []

    backups = []
    for p in sorted(backup_dir.iterdir(), reverse=True):
        try:
            size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() else p.stat().st_size
            parts = p.name.split("__")
            backups.append({
                "id":         p.name,
                "path":       str(p),
                "mod_name":   parts[0] if parts else p.name,
                "label":      parts[2] if len(parts) > 2 else "",
                "ts_str":     parts[1] if len(parts) > 1 else "",
                "created_at": p.stat().st_mtime,
                "size_bytes": size,
                "type":       "dir" if p.is_dir() else "file",
            })
        except Exception:
            pass
    return backups
