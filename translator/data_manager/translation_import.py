"""
Import existing community translations — xTranslate/SST XML and paired EN/RU string files.

The Russian Skyrim community has already translated a large share of these mods. Re-running
the LLM on strings that already have a human translation wastes time and usually lowers
quality. This module seeds the DB from existing translation sources so the pipeline only
spends model time on what's genuinely untranslated.

Two source formats:
  * xTranslate / SSTranslator XML  — <String><Source>…</Source><Dest>…</Dest></String>
  * paired string files            — <mod>_english.STRINGS + <mod>_russian.STRINGS, joined
                                      by string id (reuses scripts/strings_codec)

Matching is by source text (exact, then whitespace/case-normalized fuzzy) against the rows
already in the DB for a mod. By default it only fills *untranslated* rows — it never clobbers
existing translations unless `overwrite=True`.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from translator.data_manager.string_manager import normalize_text


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()      # strip XML namespace


def parse_xtranslate_xml(xml_text: str) -> list[tuple[str, str]]:
    """Parse xTranslate/SST XML → list of (source, dest) pairs. Namespace-tolerant; ignores
    entries with an empty source or dest."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    pairs: list[tuple[str, str]] = []
    for el in root.iter():
        if _localname(el.tag) != "string":
            continue
        src = dest = None
        for child in el:
            ln = _localname(child.tag)
            if ln == "source":
                src = (child.text or "")
            elif ln == "dest":
                dest = (child.text or "")
        if src and dest and dest.strip():
            pairs.append((src, dest))
    return pairs


def parse_string_pair(en_bytes: bytes, ru_bytes: bytes, kind: str = "STRINGS") -> list[tuple[str, str]]:
    """Join an English and a Russian string file by string id → (source, dest) pairs."""
    try:
        from scripts.strings_codec import parse_strings_bytes
    except ImportError:
        from strings_codec import parse_strings_bytes
    en = parse_strings_bytes(en_bytes, kind)
    ru = parse_strings_bytes(ru_bytes, kind)
    pairs = []
    for sid, src in en.items():
        dest = ru.get(sid)
        if src and dest and dest.strip() and dest != src:
            pairs.append((src, dest))
    return pairs


def import_pairs(repo, mod_name: str, pairs, *, source_label: str = "imported",
                 overwrite: bool = False) -> dict:
    """Apply (source, dest) pairs to a mod's DB rows. Matches by original text (exact, then
    normalized fuzzy). Returns stats: {pairs, matched, applied, skipped_existing}.

    By default only untranslated rows are filled; pass overwrite=True to replace existing
    translations too.
    """
    pairs = list(pairs)
    rows = repo.get_all_strings(mod_name)
    by_orig: dict[str, list] = {}
    by_norm: dict[str, list] = {}
    for r in rows:
        o = r.get("original") or ""
        by_orig.setdefault(o, []).append(r)
        n = normalize_text(o)
        if n:
            by_norm.setdefault(n, []).append(r)

    applied = matched = skipped = 0
    done_keys = set()
    for src, dest in pairs:
        if not (src and dest and dest.strip()):
            continue
        targets = by_orig.get(src) or by_norm.get(normalize_text(src)) or []
        if targets:
            matched += 1
        for r in targets:
            if r["key"] in done_keys:
                continue
            if not overwrite and r.get("status") == "translated" and (r.get("translation") or "").strip():
                skipped += 1
                continue
            repo.upsert(
                mod_name, r["esp_name"], r["key"], r["original"], dest, "translated",
                form_id=r.get("form_id", ""), rec_type=r.get("rec_type", ""),
                field_type=r.get("field_type", ""), field_index=r.get("field_index"),
                vmad_str_idx=r.get("vmad_str_idx", 0) or 0, source=source_label,
            )
            done_keys.add(r["key"])
            applied += 1
    return {"pairs": len(pairs), "matched": matched,
            "applied": applied, "skipped_existing": skipped}


def import_xtranslate_file(repo, mod_name: str, xml_path: Path, **kw) -> dict:
    return import_pairs(repo, mod_name, parse_xtranslate_xml(Path(xml_path).read_text("utf-8")), **kw)
