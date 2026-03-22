"""
One-time import of .trans.json files into SQLite.
Run in a background thread at startup.
"""
from __future__ import annotations
import json
import logging
import threading
from pathlib import Path

log = logging.getLogger(__name__)


def import_all_trans_json(repo, mods_dir: Path) -> None:
    """
    Walk mods_dir and import all *.trans.json files into SQLite.
    Safe to call multiple times — uses UPSERT logic.
    """
    if not mods_dir.is_dir():
        log.warning("import_all_trans_json: mods_dir not found: %s", mods_dir)
        return

    total_files = 0
    total_strings = 0

    for trans_json_path in sorted(mods_dir.rglob("*.trans.json")):
        try:
            # Mod name is the immediate subfolder of mods_dir
            parts = trans_json_path.relative_to(mods_dir).parts
            mod_name = parts[0]
            esp_name = trans_json_path.name.replace(".trans.json", ".esp")

            strings = json.loads(trans_json_path.read_text(encoding="utf-8"))
            if not isinstance(strings, list):
                continue

            n = repo.import_trans_json(mod_name, esp_name, strings)
            total_strings += n
            total_files += 1

            if total_files % 100 == 0:
                log.info("DB import progress: %d files, %d strings", total_files, total_strings)

        except Exception as exc:
            log.warning("DB import failed for %s: %s", trans_json_path, exc)

    log.info("DB import complete: %d files, %d strings total", total_files, total_strings)


def start_background_import(repo, mods_dir: Path) -> threading.Thread:
    """Start the import in a daemon thread. Returns the thread."""
    t = threading.Thread(
        target=import_all_trans_json,
        args=(repo, mods_dir),
        daemon=True,
        name="db-import",
    )
    t.start()
    return t
