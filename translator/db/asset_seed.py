"""Put MCM and SWF strings into the store, where everything else already lives.

The gap this closes
-------------------
`bulk_insert_strings` rebuilds a row's key from `(form_id, rec_type, field_type,
field_index, vmad_idx)` — the shape a plugin record has. An asset string is not keyed
that way: the scanner gives it `mcm:{rel}:{line}:{$KEY}`, `bsa-mcm:{bsa}:{rel}:{line}:{$KEY}`
or `swf:{rel}:{chid}`, because a line in a translation table has no FormID to be keyed by.
So the bootstrap in translate_pipeline skipped asset rows rather than corrupt them, and
nothing else ever inserted them: they reached SQLite only when somebody saved a
translation for one, one row at a time.

The result, measured on this install: 463 461 plugin rows and zero asset rows. Every
scope that selects them — `mcm`, `bsa`, `swf` in the UI and in the translate pipeline —
was therefore selecting from an empty set for any mod that already had plugin data, which
is all of them. It looked like "these mods have no MCM text", and they have plenty.

What this module does
---------------------
Inserts asset rows with the key the scanner produced, untouched, and with their original
text. `INSERT ... ON CONFLICT DO NOTHING`: seeding is additive and must never overwrite a
translation somebody already has.

One canonical esp_name
----------------------
`esp_name` is part of the row's identity (`UNIQUE(mod_name, esp_name, key)`), and the two
code paths that could write an asset row disagreed about it: the scanner reports the file
name, while `save_translation` took the second colon-segment of the key, which is a path.
Two spellings mean two rows for one string. `asset_esp_name()` is now the single answer,
and both paths use it. Changing it is safe exactly once — while no asset rows exist — and
that is now.
"""
from __future__ import annotations

import logging
import time
from typing import Iterable, Optional

log = logging.getLogger(__name__)

ASSET_PREFIXES = ("mcm:", "bsa-mcm:", "swf:")


def is_asset_key(key: str) -> bool:
    return bool(key) and key.startswith(ASSET_PREFIXES)


def asset_esp_name(key: str) -> Optional[str]:
    """The `esp_name` an asset row is filed under, derived from its key alone.

    The file the text lives in, as a person would name it:

        mcm:interface/translations/SkyUI_english.txt:0:$ALL   -> SkyUI_english.txt
        bsa-mcm:SkyUI.bsa:interface/.../SkyUI_english.txt:0:$ALL
                                                             -> SkyUI.bsa/SkyUI_english.txt
        swf:interface/map.swf:42                              -> map.swf

    Derived from the key rather than passed in, so a row's identity cannot depend on
    which code path happened to create it. Returns None for a plugin key.
    """
    if not is_asset_key(key):
        return None

    if key.startswith("bsa-mcm:"):
        parts = key[len("bsa-mcm:"):].split(":", 3)
        if len(parts) < 2:
            return None
        bsa, rel = parts[0], parts[1]
        return f"{bsa}/{rel.rsplit('/', 1)[-1]}"

    if key.startswith("mcm:"):
        rel = key[len("mcm:"):].split(":", 1)[0]
        return rel.rsplit("/", 1)[-1] or None

    rel = key[len("swf:"):].rpartition(":")[0]
    return rel.rsplit("/", 1)[-1] or None


def seed_asset_strings(repo, mod_name: str, strings: Iterable[dict]) -> dict:
    """Insert the asset rows out of a scanner result. Returns a per-kind count.

    `strings` is what `ModScanner.get_mod_strings()` returns; plugin rows are ignored,
    since `bulk_insert_strings` already handles those and does it better.
    """
    rows = []
    by_kind: dict[str, int] = {}
    now = time.time()

    for s in strings:
        key = s.get("key") or ""
        if not is_asset_key(key):
            continue
        esp = asset_esp_name(key)
        if not esp:
            log.debug("unparseable asset key, skipped: %s", key[:80])
            continue

        kind = key.split(":", 1)[0]
        by_kind[kind] = by_kind.get(kind, 0) + 1
        original = s.get("original") or ""
        rows.append((
            mod_name, esp, key, original,
            s.get("translation") or "",
            s.get("status") or ("translated" if s.get("translation") else "pending"),
            s.get("quality_score"),
            s.get("form_id") or "", s.get("rec_type") or "",
            s.get("field") or "TEXT", s.get("idx"), 0, now,
        ))

    if not rows:
        return {"inserted": 0, "by_kind": {}}

    before = _asset_count(repo, mod_name)
    repo.bulk_insert_asset_strings(rows)
    after = _asset_count(repo, mod_name)

    out = {"inserted": after - before, "seen": len(rows), "by_kind": by_kind}
    log.info("seeded asset strings for %s: %s", mod_name, out)
    return out


def _asset_count(repo, mod_name: str) -> int:
    row = repo.db.execute(
        "SELECT COUNT(*) AS n FROM strings WHERE mod_name=? AND ("
        "key LIKE 'mcm:%' OR key LIKE 'bsa-mcm:%' OR key LIKE 'swf:%')",
        (mod_name,)).fetchone()
    return int(row["n"] if row else 0)


def asset_counts(repo, mod_name: Optional[str] = None) -> dict:
    """How many asset rows the store holds, per kind."""
    where, params = "", ()
    if mod_name:
        where, params = "WHERE mod_name=?", (mod_name,)
    out = {}
    for kind, like in (("mcm", "mcm:%"), ("bsa-mcm", "bsa-mcm:%"), ("swf", "swf:%")):
        clause = f"{where} AND key LIKE ?" if where else "WHERE key LIKE ?"
        row = repo.db.execute(
            f"SELECT COUNT(*) AS n FROM strings {clause}", (*params, like)).fetchone()
        out[kind] = int(row["n"] if row else 0)
    return out
