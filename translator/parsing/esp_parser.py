"""
translator.parsing.esp_parser — thin wrapper around scripts.esp_engine.

Public API:
  extract_strings(path) → (strings, header)
  rewrite(src_path, dst_path, translations, mod_dir) → int
"""
from __future__ import annotations
from pathlib import Path


def extract_strings(path: Path) -> tuple[list[dict], object]:
    """Extract all translatable strings from an ESP/ESM binary.

    Returns (strings, header) where strings is a list of dicts with keys:
      key, text (original), form_id, rec_type, field_type, field_index, vmad_str_idx
    """
    from scripts.esp_engine import extract_all_strings
    return extract_all_strings(Path(path))


def rewrite(
    src_path: Path,
    dst_path: Path,
    translations: list[dict],
    mod_dir: Path | None = None,
) -> int:
    """Write translations back into an ESP binary.

    translations: list of dicts as returned by StringRepo.get_all_strings() —
                  must contain 'key', 'original' (or 'text'), 'translation'.
    Returns number of strings written.
    """
    from scripts.esp_engine import cmd_apply_from_strings
    # cmd_apply_from_strings expects 'text' key for original
    rows = []
    for r in translations:
        row = dict(r)
        row.setdefault("text", row.get("original", ""))
        rows.append(row)
    return cmd_apply_from_strings(Path(src_path), Path(dst_path), rows,
                                  mod_dir or Path(src_path).parent)
