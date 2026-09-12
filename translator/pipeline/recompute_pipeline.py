"""
RecomputePipeline — recompute quality_score and status for all translated ESP
strings in SQLite without re-translating anything.
"""
from __future__ import annotations
import logging

log = logging.getLogger(__name__)


def _revertible(original: str, translation: str) -> bool:
    """Whether replacing this translation with the English original is the right answer.

    needs_translation() says a string did not need translating, and where it is right the
    stored Russian is a mistake to undo: HairMaleElf09 rendered as «Волосы эльфа-самца
    09», or <Alias=Bruma> stored as <Alias=Брума>, which breaks the alias outright.

    But it also answers False for any all-caps word, because all-caps is usually an
    abbreviation. On the live collection that caught ALTERATION → «ИЗМЕНЕНИЕ»,
    ILLUSION → «ИЛЛЮЗИЯ» and "{0} LVL DIFF" → «{0} РАЗНИЦА УРОВНЕЙ» — three correct
    translations of magic-school labels that show in the menu, which the pass would have
    discarded for being upper case. Six strings met the branch in total and four of them
    were this.

    So reverting needs a reason of its own: the source reads as a name for the engine, or
    the translation broke a token. Neither is true of a word in capitals.
    """
    from translator.validation.quality import looks_like_identifier, validate_tokens
    if not translation or translation.strip() == (original or "").strip():
        return True                      # nothing to lose
    if looks_like_identifier(original):
        return True
    tok_ok, _ = validate_tokens(original, translation)
    return not tok_ok


class RecomputePipeline:
    """Recomputes quality scores and statuses from SQLite."""

    def __init__(self, cfg, repo):
        self._cfg  = cfg
        self._repo = repo

    def _load_terms(self) -> dict:
        """The curated glossary, or {} — a missing file turns terminology checking off
        rather than failing the run, and the job log says which happened."""
        import json
        from pathlib import Path
        try:
            path = self._cfg.paths.skyrim_terms
            if path and Path(path).exists():
                return json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("recompute: could not load glossary, enforcement is off: %s", exc)
        return {}

    def run(self, job, mod_name: str | None = None) -> None:
        from scripts.esp_engine import compute_string_status as _css, needs_translation as _needs_trans
        from translator.web.job_manager import JobManager

        jm = JobManager.get()
        repo = self._repo

        if not repo:
            job.add_log("ERROR: no repo — cannot recompute scores without SQLite")
            return

        # The whole point of a recompute is to re-judge with everything the gate knows
        # today, and the gate's two strongest checks are the ones that need arguments:
        # the glossary, and a full stop on the end of a name. Run without them and this
        # pass silently agrees with 3 932 of the 7 861 strings it exists to catch.
        terms = self._load_terms()
        job.add_log(f"Glossary: {len(terms)} term(s) enforced"
                    if terms else "Glossary: NOT loaded — terminology will not be checked")

        # The database, not the mods directory. A recompute re-judges rows, and a row
        # belongs to a mod_name in the store — which is not always a folder on disk under
        # that exact name. Walking the directory silently skipped whole mods: 1 217
        # settled record names stayed in review through three full runs because the
        # folders holding them were never visited.
        if mod_name:
            mod_names = [mod_name]
        else:
            mod_names = [r[0] for r in repo.db.execute(
                "SELECT DISTINCT mod_name FROM strings ORDER BY mod_name").fetchall()]

        total = len(mod_names)
        updated = skipped = 0

        job.add_log(f"Recomputing scores for {total} mod(s) from SQLite...")
        jm.update_progress(job, 0, total, "Starting...")

        for i, _mod in enumerate(mod_names):
            jm.update_progress(job, i, total, _mod)
            try:
                rows = repo.get_all_strings(_mod)
                esp_rows = [
                    r for r in rows
                    if not any(r["key"].startswith(p) for p in ("mcm:", "bsa-mcm:", "swf:"))
                ]
                n_changed = n_review = n_archived = 0
                for r in esp_rows:
                    orig  = r.get("original", "") or ""
                    trans = r.get("translation", "") or ""
                    # Already settled: a record name whose answer is itself. The repair
                    # pass marks these, and nothing then set the status to match — 1 217
                    # of them sat in review carrying source='untranslatable', re-dispatched
                    # by every sweep and answered the same way each time.
                    if (r.get("source") == "untranslatable"
                            and trans.strip() == orig.strip()):
                        if r.get("status") == "translated" and r.get("quality_score") == 100:
                            continue
                        new_qs, new_status, new_trans = 100, "translated", orig
                    elif not _needs_trans(orig) and _revertible(orig, trans):
                        new_qs, new_status, new_trans = 100, "translated", orig
                        if (trans == orig and r.get("quality_score") == 100
                                and r.get("status") == "translated"):
                            continue
                        # repo.upsert keeps no history, so archive the translation we are
                        # about to discard: the string history view can show and restore it.
                        if trans and trans != orig and r.get("id") is not None:
                            try:
                                repo.insert_history(
                                    r["id"], trans, r.get("status") or "translated",
                                    r.get("quality_score"), "recompute-discarded", None, None)
                                n_archived += 1
                            except Exception as exc:
                                log.warning("recompute: could not archive %s/%s: %s",
                                            _mod, r.get("key"), exc)
                    else:
                        new_trans = trans
                        if not trans:
                            continue
                        new_qs, _, _, new_status = _css(
                            orig, trans, terms,
                            r.get("rec_type") or None, r.get("field_type") or None)
                    if new_status == "needs_review":
                        n_review += 1
                    if (r.get("quality_score") != new_qs or r.get("status") != new_status
                            or trans != new_trans):
                        repo.upsert(
                            mod_name=_mod,
                            esp_name=r["esp_name"],
                            key=r["key"],
                            original=orig,
                            translation=new_trans,
                            status=new_status,
                            quality_score=new_qs,
                            form_id=r.get("form_id") or "",
                            rec_type=r.get("rec_type") or "",
                            field_type=r.get("field_type") or "",
                            field_index=r.get("field_index"),
                            vmad_str_idx=r.get("vmad_str_idx") or 0,
                        )
                        n_changed += 1
                if n_changed:
                    updated += 1
                    _arch = f", {n_archived} prior translation(s) archived to history" if n_archived else ""
                    job.add_log(
                        f"Updated {_mod}: {n_changed} strings recomputed, "
                        f"{n_review} needs_review{_arch}"
                    )
                else:
                    skipped += 1
            except Exception as exc:
                job.add_log(f"ERROR {_mod}: {exc}")

        jm.update_progress(job, total, total, "Done")
        job.result = f"Recomputed scores: {updated} mod(s) updated, {skipped} unchanged"
        job.add_log(job.result)
