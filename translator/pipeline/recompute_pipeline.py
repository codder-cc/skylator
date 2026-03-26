"""
RecomputePipeline — recompute quality_score and status for all translated ESP
strings in SQLite without re-translating anything.
"""
from __future__ import annotations
import logging

log = logging.getLogger(__name__)


class RecomputePipeline:
    """Recomputes quality scores and statuses from SQLite."""

    def __init__(self, cfg, repo):
        self._cfg  = cfg
        self._repo = repo

    def run(self, job, mod_name: str | None = None) -> None:
        from scripts.esp_engine import compute_string_status as _css, needs_translation as _needs_trans
        from translator.web.job_manager import JobManager

        jm = JobManager.get()
        repo = self._repo

        if not repo:
            job.add_log("ERROR: no repo — cannot recompute scores without SQLite")
            return

        mods_dir = self._cfg.paths.mods_dir
        mod_names = [mod_name] if mod_name else [p.name for p in mods_dir.iterdir() if p.is_dir()]

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
                n_changed = n_review = 0
                for r in esp_rows:
                    orig  = r.get("original", "") or ""
                    trans = r.get("translation", "") or ""
                    if not _needs_trans(orig):
                        new_qs, new_status, new_trans = 100, "translated", orig
                        if (trans == orig and r.get("quality_score") == 100
                                and r.get("status") == "translated"):
                            continue
                    else:
                        new_trans = trans
                        if not trans:
                            continue
                        new_qs, _, _, new_status = _css(orig, trans)
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
                    job.add_log(
                        f"Updated {_mod}: {n_changed} strings recomputed, {n_review} needs_review"
                    )
                else:
                    skipped += 1
            except Exception as exc:
                job.add_log(f"ERROR {_mod}: {exc}")

        jm.update_progress(job, total, total, "Done")
        job.result = f"Recomputed scores: {updated} mod(s) updated, {skipped} unchanged"
        job.add_log(job.result)
