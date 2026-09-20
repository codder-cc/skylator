"""Translate From Mod — take a published translation and fold it into our strings.

Somebody has already translated this mod. Their work is a finished human translation of
exactly the records we are staring at, which is worth more than anything a model will
produce for them, and it is free. This module lines their plugin up against ours and
says, record by record, what could be taken.

How the two sides line up
-------------------------
Never by text similarity. The donor's text is in the target language and ours is in the
source, so there is nothing to compare; every match here is an identity match.

* **Plugins** -- a translation mod is the original plugin with its text replaced, so the
  records keep their FormIDs: same plugin, same
  `(form_id, rec_type, field_type, field_index)`, therefore the same string. The one
  thing that moves is the high byte of a FormID, which indexes the plugin's own master
  list; it shifts only if the donor added or dropped a master. `local_id` matching covers
  that by comparing the low 24 bits, and only where doing so is unambiguous on both sides.
* **MCM tables** -- keyed by `$KEY` within a table, *not* by line number. Translators
  reorder and re-comment these files freely, so matching by position would miss almost
  everything while looking like it worked.
* **SWF text** -- keyed by DefineText character id within a file name. The path inside
  the mod differs between uploads; the ids do not.

Our own rows encode all three in one `key` column, so the indexes below are built by
parsing that column back apart. Whatever a row is, it is matched by the same tuple shape
the harvester produces.

What it refuses to do
---------------------
A donor that is not actually translated is the dangerous case: some "translation"
uploads ship the untouched English plugin alongside the translated one, and folding that
in would overwrite real translations with the original text while reporting a big
successful merge. Every donor string is checked for target-language characters and
against our own original before it is allowed to count.

Nothing is written by `plan()`. Deciding and doing are separate calls so the UI can show
what would happen -- including the conflicts -- before anything touches the store.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Iterable, Optional

log = logging.getLogger(__name__)

# What a translated string has to contain to be believable, per target language. A donor
# string of pure punctuation or digits is exempt -- "100%" is a legitimate translation
# of "100%" and carries no letters to check.
_SCRIPT_PATTERNS = {
    "russian":    re.compile(r"[а-яё]", re.IGNORECASE),
    "ukrainian":  re.compile(r"[а-яіїєґ]", re.IGNORECASE),
    "belarusian": re.compile(r"[а-яёіў]", re.IGNORECASE),
    "bulgarian":  re.compile(r"[а-я]", re.IGNORECASE),
    "serbian":    re.compile(r"[а-шђјљњћџ]", re.IGNORECASE),
    "japanese":   re.compile(r"[぀-ヿ一-鿿]"),
    "korean":     re.compile(r"[가-힯]"),
    "chinese":    re.compile(r"[一-鿿]"),
    "greek":      re.compile(r"[Ͱ-Ͽ]"),
}

_HAS_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)

# Actions a candidate can carry. Ordered by how much attention each deserves.
FILL     = "fill"        # we have no translation — pure gain
CONFLICT = "conflict"    # we have a different translation — a decision
SAME     = "same"        # identical to what we already have — nothing to do
REJECTED = "rejected"    # the donor string is not a translation
UNMATCHED = "unmatched"  # no record of ours corresponds


@dataclass
class Candidate:
    """One donor string weighed against one of our rows."""
    esp_name:    str
    key:         str
    action:      str
    donor_text:  str
    original:    str = ""
    current:     str = ""
    status:      str = ""
    match:       str = ""     # "exact" | "local_id"
    reason:      str = ""     # why a rejection was a rejection
    string_id:   Optional[int] = None
    kind:        str = "esp"  # "esp" | "mcm" | "bsa-mcm" | "swf"

    def as_dict(self) -> dict:
        return {"esp_name": self.esp_name, "key": self.key, "action": self.action,
                "donor_text": self.donor_text, "original": self.original,
                "current": self.current, "status": self.status, "match": self.match,
                "reason": self.reason, "string_id": self.string_id, "kind": self.kind}


@dataclass
class MergePlan:
    """What a merge would do. Produced without writing anything."""
    mod_name:    str
    language:    str
    candidates:  list = field(default_factory=list)
    donor_total: int = 0
    our_total:   int = 0

    def by_action(self, action: str) -> list:
        return [c for c in self.candidates if c.action == action]

    @property
    def counts(self) -> dict:
        out: dict = {}
        for c in self.candidates:
            out[c.action] = out.get(c.action, 0) + 1
        return out

    @property
    def usable(self) -> int:
        return len(self.by_action(FILL)) + len(self.by_action(CONFLICT))

    @property
    def counts_by_kind(self) -> dict:
        out: dict = {}
        for c in self.candidates:
            bucket = out.setdefault(c.kind, {})
            bucket[c.action] = bucket.get(c.action, 0) + 1
        return out

    def as_dict(self, sample: int = 200) -> dict:
        return {
            "mod_name": self.mod_name, "language": self.language,
            "donor_total": self.donor_total, "our_total": self.our_total,
            "counts": self.counts, "counts_by_kind": self.counts_by_kind,
            "usable": self.usable,
            # The whole list can be tens of thousands of rows; the UI pages the rest.
            "candidates": [c.as_dict() for c in self.candidates[:sample]],
            "truncated": max(0, len(self.candidates) - sample),
        }


# -- language sanity -------------------------------------------------------------


def looks_translated(text: str, original: str, language: str) -> tuple[bool, str]:
    """Whether `text` is plausibly `original` rendered into `language`.

    Cheap and deliberately shallow: this is a guard against folding in an untranslated
    plugin, not a quality judgement. Quality is scored later by the existing validator.
    """
    text = (text or "").strip()
    if not text:
        return False, "empty"
    if original and text == original.strip():
        return False, "identical to the original"

    pattern = _SCRIPT_PATTERNS.get((language or "").strip().lower())
    if pattern is None:
        # An unknown target language cannot be checked by script; "different from the
        # original" is then the only honest test, and it already passed.
        return True, ""
    if pattern.search(text):
        return True, ""
    if not _HAS_LETTER.search(text):
        # "100%", "---", "50/50" — nothing to translate, and passing it through is right.
        return True, ""
    return False, f"no {language} characters"


# -- indexing --------------------------------------------------------------------


def _local_key(row_key: str, form_id: str, rec_type: str, field_type: str,
               field_index, vmad_str_idx) -> str:
    """The key with the master-index byte of the FormID masked off."""
    fid = (form_id or "").strip()
    local = fid[-6:].upper() if len(fid) >= 6 else fid.upper()
    return str((local, rec_type, field_type, field_index, vmad_str_idx or 0))


def row_match_id(row: dict) -> Optional[tuple]:
    """The identity tuple for one of our rows, or None if it has no usable one.

    Our `key` column carries three different encodings, produced by mod_scanner:

        mcm:{rel_txt}:{line_idx}:{$KEY}
        bsa-mcm:{bsa_rel}:{rel_in_cache}:{line_idx}:{$KEY}
        swf:{swf_rel}:{chid}
        str((form_id, rec_type, field_type, field_index, vmad_idx))    -- a plugin

    Paths here are relative and use forward slashes, so they never contain a colon;
    that is what makes a bounded split safe. An MCM key is split off last precisely
    because it is the one field that could contain one.
    """
    from translator.nexus.harvest import table_stem

    key = row.get("key") or ""

    if key.startswith("bsa-mcm:"):
        parts = key[len("bsa-mcm:"):].split(":", 3)
        if len(parts) == 4 and parts[2].isdigit():
            return ("bsa-mcm", table_stem(parts[1].rsplit("/", 1)[-1]), parts[3])
        return None

    if key.startswith("mcm:"):
        parts = key[len("mcm:"):].split(":", 2)
        if len(parts) == 3 and parts[1].isdigit():
            return ("mcm", table_stem(parts[0].rsplit("/", 1)[-1]), parts[2])
        return None

    if key.startswith("swf:"):
        rel, _, chid = key[len("swf:"):].rpartition(":")
        if rel and chid:
            return ("swf", rel.rsplit("/", 1)[-1].lower(), chid)
        return None

    return ("esp", (row.get("esp_name") or "").lower(), key)


def _index_ours(rows: Iterable[dict]) -> tuple[dict, dict]:
    """Index our rows by identity, and plugins additionally by master-insensitive id.

    The second index drops any key that is not unique: if two records collide once the
    master byte is masked, taking either would be a guess, and a guess written into the
    translation store is indistinguishable from a fact later.
    """
    exact: dict[tuple, dict] = {}
    local: dict[tuple, dict] = {}
    collided: set = set()

    for r in rows:
        mid = row_match_id(r)
        if mid is None:
            continue
        exact[mid] = r
        if mid[0] != "esp":
            continue                     # only a FormID has a master byte to mask
        lk = ("esp", mid[1],
              _local_key(r["key"], r.get("form_id", ""), r.get("rec_type", ""),
                         r.get("field_type", ""), r.get("field_index"),
                         r.get("vmad_str_idx", 0)))
        if lk in local:
            collided.add(lk)
        else:
            local[lk] = r
    for lk in collided:
        local.pop(lk, None)
    return exact, local


# -- planning --------------------------------------------------------------------


def plan(
    repo,
    mod_name: str,
    donor_plugins: list,
    language: str = "Russian",
    esp_map: Optional[dict] = None,
    allow_local_id: bool = True,
) -> MergePlan:
    """Work out what a donor could contribute, without writing anything.

    `esp_map` renames donor plugins onto ours for the case where the translation was
    uploaded under a different filename -- rare, but it costs one dict to support and
    the alternative is the whole merge silently matching nothing.
    """
    ours = repo.get_all_strings(mod_name)
    exact_ix, local_ix = _index_ours(ours)
    result = MergePlan(mod_name=mod_name, language=language, our_total=len(ours))

    for dp in donor_plugins:
        for ds in dp.strings:
            result.donor_total += 1
            mid = ds.match_id
            # A donor plugin uploaded under a different filename is mapped onto ours.
            if ds.kind == "esp":
                mapped = (esp_map or {}).get(ds.esp_name)
                if mapped:
                    mid = ("esp", mapped.lower(), ds.key)
            target_esp = ds.esp_name or ds.origin

            row = exact_ix.get(mid)
            how = "exact"

            if row is None and allow_local_id and ds.kind == "esp":
                lk = ("esp", mid[1], _local_key(ds.key, ds.form_id, ds.rec_type,
                                                ds.field_type, ds.field_index, 0))
                row = local_ix.get(lk)
                how = "local_id"

            if row is None:
                result.candidates.append(Candidate(
                    esp_name=target_esp, key=ds.key or str(mid), action=UNMATCHED,
                    donor_text=ds.text, kind=ds.kind))
                continue

            ok, why = looks_translated(ds.text, row.get("original", ""), language)
            if not ok:
                result.candidates.append(Candidate(
                    esp_name=row["esp_name"], key=row["key"], action=REJECTED,
                    donor_text=ds.text, original=row.get("original", ""),
                    current=row.get("translation", "") or "",
                    status=row.get("status", ""), match=how, reason=why,
                    string_id=row.get("id"), kind=ds.kind))
                continue

            current = (row.get("translation") or "").strip()
            if current == ds.text.strip():
                action = SAME
            elif current:
                action = CONFLICT
            else:
                action = FILL

            result.candidates.append(Candidate(
                esp_name=row["esp_name"], key=row["key"], action=action,
                donor_text=ds.text, original=row.get("original", ""),
                current=current, status=row.get("status", ""), match=how,
                string_id=row.get("id"), kind=ds.kind))

    log.info("merge plan for %s from %d donor strings: %s",
             mod_name, result.donor_total, result.counts)
    return result


# -- applying --------------------------------------------------------------------


def apply(
    repo,
    merge_plan: MergePlan,
    overwrite: bool = False,
    status: str = "needs_review",
    only_keys: Optional[Iterable[tuple]] = None,
    source: str = "nexus-translation",
    global_dict=None,
) -> dict:
    """Write the plan's translations into the store.

    `overwrite=False` takes only the rows we have nothing for, which is the safe default:
    a donor is a second opinion, not an authority, and quietly replacing finished work
    with it is the one outcome nobody asked for. `status` decides whether what lands is
    presented for review or accepted outright.

    `only_keys` narrows the write to an explicit set of (esp_name, key) the user ticked,
    so the same plan serves "take everything" and "take these four".
    """
    if status not in ("needs_review", "translated"):
        raise ValueError(f"status must be needs_review or translated, not {status!r}")

    # JSON не знает кортежей: тикнутые в интерфейсе строки приезжают как ["esp", "key"],
    # и `set(only_keys)` на них падает с «unhashable type: 'list'» — то есть ровно та
    # форма запроса, которую документирует /transfer/apply, не работала. Приводим пары
    # к кортежам здесь: это функция, чей договор — «набор (esp_name, key)», и терпимой
    # к форме должна быть она, а не каждый вызывающий.
    wanted = None
    if only_keys is not None:
        wanted = set()
        for pair in only_keys:
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                wanted.add((pair[0], pair[1]))
            else:
                # Молча пропустить — значит «применено 0» без объяснения; лучше сказать.
                raise ValueError(f"only_keys wants (esp_name, key) pairs, got {pair!r}")
    applied = skipped = dict_added = 0
    touched_esps: set = set()

    for c in merge_plan.candidates:
        if c.action not in (FILL, CONFLICT):
            continue
        if c.action == CONFLICT and not overwrite:
            skipped += 1
            continue
        if wanted is not None and (c.esp_name, c.key) not in wanted:
            skipped += 1
            continue

        repo.upsert(
            mod_name = merge_plan.mod_name,
            esp_name = c.esp_name,
            key      = c.key,
            original = c.original,
            translation = c.donor_text,
            status   = status,
            # The donor's provenance travels with the row: a translation taken from
            # someone else's mod should never be mistaken later for our own output.
            source   = source,
            translated_by = source,
            translated_at = time.time(),
        )
        applied += 1
        touched_esps.add(c.esp_name)

        # The pair is worth more than this one mod: an English original with a human
        # Russian rendering is exactly what the cross-mod dictionary is for, and what
        # gives the model real terminology to hold on to elsewhere.
        if global_dict is not None and c.original:
            try:
                global_dict.add(c.original, c.donor_text)
                dict_added += 1
            except Exception:
                log.debug("global dict rejected a pair", exc_info=True)

    if global_dict is not None and dict_added:
        try:
            global_dict.save()
        except Exception:
            log.warning("could not persist the global dictionary", exc_info=True)

    out = {"applied": applied, "skipped": skipped, "status": status,
           "overwrite": overwrite, "dict_entries": dict_added,
           "esps": sorted(touched_esps)}
    log.info("merge applied to %s: %s", merge_plan.mod_name, out)
    return out
