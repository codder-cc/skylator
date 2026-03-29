"""
TranslatePipeline — 12-step translation pipeline for a single mod.

Wires together StringManager, ReservationManager, TranslationCache,
StatsManager, ContextBuilder, and WorkerPool into a clean sequence.

Steps:
  1.  Resolve strings (filtered by scope + TranslationMode)
  2.  Mark untranslatable strings
  3.  Skip already-reserved strings
  4.  Acquire reservations
  5.  Cache lookup (TranslationCache / DB hash dedup)
  6.  Dict lookup (GlobalDict compat layer)
  7.  Build mod context
  8.  Dispatch to WorkerPool
  (per-string callback):
  9.  Validate (already done inside WorkerPool via compute_string_status)
  10. Save via StringManager
  11. Invalidate StatsManager cache
  12. Notify SSE via JobManager.add_string_update
  (finally):
      Release reservations
      Save GlobalDict
      Recompute StatsManager
"""
from __future__ import annotations
import logging
from enum import Enum
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


class TranslationMode(str, Enum):
    UNTRANSLATED = "untranslated"   # only status='pending' (default)
    NEEDS_REVIEW = "needs_review"   # only status='needs_review'
    FORCE_ALL    = "force_all"      # re-translate all strings


class DeployMode(str, Enum):
    ALL               = "all"
    SKIP_UNTRANSLATED = "skip_untranslated"
    SKIP_PARTIAL      = "skip_partial"
    SKIP_ISSUES       = "skip_issues"   # skip mods with needs_review > 0


class TranslatePipeline:
    """Executes all 12 translation steps for a single mod."""

    def __init__(
        self,
        cfg,
        repo,
        string_mgr,
        reservation_mgr,
        translation_cache,
        stats_mgr,
        global_dict=None,
    ):
        self._cfg              = cfg
        self._repo             = repo
        self._string_mgr       = string_mgr
        self._reservation_mgr  = reservation_mgr
        self._translation_cache = translation_cache
        self._stats_mgr        = stats_mgr
        self._global_dict      = global_dict

    def run(
        self,
        job,
        mod_name: str,
        scope: str = "all",
        mode: TranslationMode = TranslationMode.UNTRANSLATED,
        backends=None,
        params=None,
        keys: Optional[list] = None,
    ) -> None:
        """Run all 12 pipeline steps.  Called from translate_strings_worker shim."""
        from translator.web.job_manager import JobManager
        from translator.web.worker_pool import WorkerPool
        from translator.context.builder import ContextBuilder
        from translator.prompt.builder import TranslationMemory, enrich_context
        from scripts.esp_engine import prepare_for_ai, needs_translation as _needs_trans

        jm = JobManager.get()

        # ── Step 1: Resolve strings ───────────────────────────────────────────
        strings = self._resolve_strings(mod_name, scope, mode, keys, jm, job)
        job.add_log(f"[DEBUG] After resolve: {len(strings)} strings (scope={scope!r}, mode={mode})")

        # ── Step 2: Mark untranslatable ───────────────────────────────────────
        n_untrans = self._string_mgr.mark_untranslatable(mod_name)
        if n_untrans:
            job.add_log(f"Marked {n_untrans} untranslatable strings")
            # Re-filter after marking
            strings = [s for s in strings if s.get("status") != "translated"
                       or mode == TranslationMode.FORCE_ALL]
            job.add_log(f"[DEBUG] After untranslatable filter: {len(strings)} strings")

        # ── Steps 3 & 4: Skip reserved + acquire reservations ────────────────
        _reservation_mgr = self._reservation_mgr
        if _reservation_mgr and strings:
            reserved_ids = _reservation_mgr.get_reserved_string_ids(mod_name)
            before = len(strings)
            strings = [s for s in strings if s.get("id") not in reserved_ids]
            if len(reserved_ids) > 0:
                job.add_log(f"[DEBUG] Reservation pre-filter: {before} → {len(strings)} (reserved={len(reserved_ids)})")

            string_ids = [s["id"] for s in strings if s.get("id")]
            if string_ids:
                machine_label = backends[0][0] if backends else job.name
                acq = _reservation_mgr.acquire_batch(string_ids, machine_label, job.id)
                if acq.already_taken:
                    job.add_log(f"Skipped {len(acq.already_taken)} strings reserved by another job")
                acquired_set = set(acq.reserved)
                strings = [s for s in strings if not s.get("id") or s["id"] in acquired_set]

        try:
            # ── Step 5: Cache lookup ──────────────────────────────────────────
            if self._translation_cache and strings and mode != TranslationMode.FORCE_ALL:
                before_cache = len(strings)
                strings = self._apply_cache_hits(strings, jm, job, mod_name)
                if len(strings) != before_cache:
                    job.add_log(f"[DEBUG] Cache resolved {before_cache - len(strings)} strings; {len(strings)} remain")

            # ── Step 6: GlobalDict lookup (compat layer) ──────────────────────
            gd = self._global_dict
            gd_dirty = False
            if gd and strings and mode != TranslationMode.FORCE_ALL:
                before_dict = len(strings)
                strings, gd_dirty_flag = self._apply_dict_hits(strings, jm, job, mod_name)
                gd_dirty = gd_dirty_flag
                if len(strings) != before_dict:
                    job.add_log(f"[DEBUG] Dict resolved {before_dict - len(strings)} strings; {len(strings)} remain")

            if not strings:
                job.add_log(f"[DEBUG] 0 strings remain — exiting early (all resolved or none matched filter)")
                jm.update_progress(job, 1, 1, "Done — all strings resolved from cache/dict")
                job.result = f"All strings resolved from cache/dict for {mod_name}"
                return

            total = len(strings)

            # ── Step 7: Build context ─────────────────────────────────────────
            jm.update_progress(job, 0, total, f"Building context for {mod_name}...")
            mod_folder = self._cfg.paths.mods_dir / mod_name
            context = ContextBuilder().get_mod_context(mod_folder, force=False)

            # ── TM: seed from all previously-translated strings in this mod ──────
            # Uses a word-indexed structure so per-chunk lookup is O(words_in_chunk)
            # instead of O(all_pairs).  Also captures translations from prior job
            # runs (not just the current batch), giving consistency on re-runs.
            tm = TranslationMemory()
            if self._repo and self._repo.mod_has_data(mod_name):
                for r in self._repo.get_all_strings(mod_name):
                    tm.add(r.get("original") or "", r.get("translation") or "")
            # Also seed from strings already carrying translations (e.g. FORCE_ALL mode)
            for s in strings:
                tm.add(s.get("original", ""), s.get("translation", ""))
            if len(tm):
                job.add_log(f"TM: {len(tm)} pairs loaded for {mod_name}")

            def _build_chunk_context(originals: list[str]) -> str:
                ai_preview, _ = prepare_for_ai(originals)
                return enrich_context(context, tm.build_block(ai_preview), ai_preview)

            # ── Step 8: Dispatch to WorkerPool ───────────────────────────────
            # Steps 9-12 happen inside the on_string_done callback below.
            if not backends:
                from translator.web.job_manager import JobStatus
                job.status = JobStatus.PAUSED
                job.add_log(
                    f"Paused — no inference backend available for {mod_name}. "
                    f"Assign a remote worker and Resume to continue."
                )
                return

            n_failed = [0]

            def _on_string_done(s: dict, r: dict) -> None:
                nonlocal gd_dirty
                if r.get("skipped"):
                    return
                if not r.get("translation"):
                    n_failed[0] += 1
                    job.add_log(
                        f"[WARN] Empty AI response for {s.get('esp','?')} [{s.get('key','?')}]"
                        f" — string left as pending"
                    )
                    return
                translation = r["translation"]
                if r.get("token_issues"):
                    job.add_log(f"Token mismatch [{s['key']}]: {'; '.join(r['token_issues'])}")

                actual_label = r.get("machine_label") or (backends[0][0] if backends else "")

                # Step 10: Save via StringManager
                result = self._string_mgr.save_string(
                    mod_name=mod_name,
                    esp_name=s["esp"],
                    key=s["key"],
                    translation=translation,
                    original=s.get("original", ""),
                    source="ai",
                    machine_label=actual_label,
                    job_id=job.id,
                    quality_score=r.get("quality_score"),
                    status=r.get("status"),
                )

                # Step 11: Invalidate stats cache
                if self._stats_mgr:
                    try:
                        self._stats_mgr.invalidate(mod_name)
                    except Exception:
                        pass

                # Step 12: Notify SSE
                jm.add_string_update(
                    job, s["key"], s["esp"],
                    translation, result.status, result.quality_score,
                    source="ai",
                    machine_label=actual_label,
                )
                tm.add(s.get("original", ""), translation)
                if gd:
                    gd.add(s.get("original", ""), translation)
                    gd_dirty = True

            def _on_status(statuses) -> None:
                job._worker_statuses = {st.label: st.to_dict() for st in statuses}

            pool = WorkerPool(backends, chunk_size=10)
            pool_result = pool.run(
                strings         = strings,
                context         = context,
                params          = params,
                force           = (mode == TranslationMode.FORCE_ALL),
                on_string_done  = _on_string_done,
                on_progress     = lambda done, tot: jm.update_progress(
                    job, done, tot, f"Translating {done}/{tot}"),
                on_status       = _on_status,
                should_stop     = lambda: job.status.value in ("cancelled", "paused"),
                context_builder = _build_chunk_context,
            )

            if gd and gd_dirty:
                gd.save()

            from translator.web.job_manager import JobStatus
            # If job was paused mid-run (user-requested or dead worker), don't mark it done
            if job.status == JobStatus.PAUSED:
                return

            # If the pool processed fewer strings than expected, a backend died —
            # pause so the user can reassign a worker and Resume.
            pool_done = pool_result.get("done", 0)
            if pool_done < total and job.status == JobStatus.RUNNING:
                unprocessed = total - pool_done
                job.status = JobStatus.PAUSED
                job.add_log(
                    f"Paused — {unprocessed} string(s) not processed (worker disconnected or timed out). "
                    f"Assign a worker and Resume to retry."
                )
                return

            jm.update_progress(job, total, total, "Done")
            if n_failed[0]:
                job.result = (
                    f"Translated strings for {mod_name} "
                    f"({n_failed[0]} strings got empty AI response — re-run to retry)"
                )
                job.add_log(
                    f"[WARN] {n_failed[0]} string(s) returned empty from AI and remain pending. "
                    f"Check the worker logs for details. Re-run the job to retry."
                )
            else:
                job.result = f"Translated strings for {mod_name}"

        finally:
            # Always release reservations (even on cancel / exception)
            if _reservation_mgr:
                try:
                    _reservation_mgr.release_batch(job.id)
                except Exception as exc:
                    log.warning("release_batch failed: %s", exc)
            # Recompute stats after job
            if self._stats_mgr:
                try:
                    self._stats_mgr.recompute(mod_name)
                except Exception as exc:
                    log.warning("stats recompute failed: %s", exc)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _resolve_strings(self, mod_name, scope, mode, keys, jm, job) -> list:
        """Step 1: Load strings from SQLite and apply scope/mode filters."""
        from translator.web.mod_scanner import ModScanner
        from translator.web.asset_cache import BsaStringCache, SwfStringCache
        from scripts.esp_engine import needs_translation as _needs_trans
        import ast
        from collections import defaultdict

        repo = self._repo
        cfg  = self._cfg

        if repo and repo.mod_has_data(mod_name):
            db_rows = repo.get_all_strings(mod_name)
            strings = [{
                "id":            r["id"],
                "esp":           r["esp_name"],
                "key":           r["key"],
                "original":      r.get("original") or "",
                "translation":   r.get("translation") or "",
                "status":        r.get("status") or "pending",
                "quality_score": r.get("quality_score"),
                "form_id":       r.get("form_id") or "",
                "rec_type":      r.get("rec_type") or "",
                "field":         r.get("field_type") or "",
                "idx":           r.get("field_index"),
                "dict_match":    "",
            } for r in db_rows]
            job.add_log(f"Loaded {len(strings)} strings from SQLite for {mod_name}")
        else:
            # Bootstrap from filesystem
            _cache_root = cfg.paths.temp_dir if cfg.paths.temp_dir else cfg.paths.translation_cache.parent.parent / "temp"
            bsa_cache = BsaStringCache(
                cache_root=_cache_root,
                bsarch_exe=str(cfg.paths.bsarch_exe) if cfg.paths.bsarch_exe else None,
            )
            swf_cache = SwfStringCache(
                cache_root=_cache_root,
                ffdec_jar=str(cfg.paths.ffdec_jar) if cfg.paths.ffdec_jar else None,
            )
            scanner = ModScanner(cfg.paths.mods_dir, cfg.paths.translation_cache,
                                 cfg.paths.nexus_cache)
            strings = scanner.get_mod_strings(mod_name, bsa_cache=bsa_cache, swf_cache=swf_cache)
            job.add_log(f"Bootstrap: loaded {len(strings)} strings for {mod_name}")

            if repo and strings:
                by_esp: dict = defaultdict(list)
                for s in strings:
                    if any(s["key"].startswith(p) for p in ("mcm:", "bsa-mcm:", "swf:")):
                        continue
                    orig = s.get("original", "")
                    if not _needs_trans(orig):
                        trans, st, qs = orig, "translated", 100
                    else:
                        trans = s.get("translation", "")
                        st    = s.get("status", "pending")
                        qs    = s.get("quality_score")
                    key = s.get("key", "")
                    try:
                        parsed   = ast.literal_eval(key) if key.startswith("(") else None
                        vmad_idx = int(parsed[4]) if parsed and len(parsed) > 4 else 0
                    except Exception:
                        vmad_idx = 0
                    by_esp[s["esp"]].append({
                        "form_id": s.get("form_id"), "rec_type": s.get("rec_type"),
                        "field_type": s.get("field"), "field_index": s.get("idx"),
                        "vmad_str_idx": vmad_idx, "text": orig,
                        "translation": trans, "status": st, "quality_score": qs,
                    })
                for esp_name, esp_rows in by_esp.items():
                    repo.bulk_insert_strings(mod_name, esp_name, esp_rows)
                # Reload from DB so strings have IDs
                db_rows = repo.get_all_strings(mod_name)
                strings = [{
                    "id": r["id"], "esp": r["esp_name"], "key": r["key"],
                    "original": r.get("original") or "", "translation": r.get("translation") or "",
                    "status": r.get("status") or "pending", "quality_score": r.get("quality_score"),
                    "form_id": r.get("form_id") or "", "rec_type": r.get("rec_type") or "",
                    "field": r.get("field_type") or "", "idx": r.get("field_index"),
                    "dict_match": "",
                } for r in db_rows]
                job.add_log(f"Bootstrapped SQLite: {sum(len(v) for v in by_esp.values())} strings")

        # Key filter
        if keys:
            key_set = set(keys)
            strings = [s for s in strings if s["key"] in key_set]
        else:
            _non_esp = ("mcm:", "bsa-mcm:", "swf:")
            if scope == "esp":
                strings = [s for s in strings if not any(s["key"].startswith(p) for p in _non_esp)]
            elif scope == "mcm":
                strings = [s for s in strings if s["key"].startswith("mcm:")]
            elif scope == "bsa":
                strings = [s for s in strings if s["key"].startswith("bsa-mcm:")]
            elif scope == "swf":
                strings = [s for s in strings if s["key"].startswith("swf:")]
            elif scope == "review":
                strings = [s for s in strings if s.get("status") == "needs_review"]

            # Mode filter
            if mode == TranslationMode.UNTRANSLATED:
                strings = [s for s in strings if not s["translation"]]
            elif mode == TranslationMode.NEEDS_REVIEW:
                strings = [s for s in strings if s.get("status") == "needs_review"]
            # FORCE_ALL: keep everything

            strings = [s for s in strings if not s["original"].startswith("[LOC:")]

        return strings

    def _apply_cache_hits(self, strings, jm, job, mod_name) -> list:
        """Step 5: Look up translations via TranslationCache. Save hits inline."""
        hits = self._translation_cache.bulk_lookup([s["original"] for s in strings])
        remaining = []
        cache_saved = 0
        for s in strings:
            t = hits.get(s["original"])
            if t:
                self._string_mgr.save_string(
                    mod_name=mod_name, esp_name=s["esp"], key=s["key"],
                    translation=t, original=s.get("original", ""),
                    source="cache", job_id=job.id,
                )
                jm.add_string_update(job, s["key"], s["esp"], t, "translated", None, source="cache")
                cache_saved += 1
            else:
                remaining.append(s)
        if cache_saved:
            job.add_log(f"Reused {cache_saved} translations from cache")
        return remaining

    def _apply_dict_hits(self, strings, jm, job, mod_name) -> tuple[list, bool]:
        """Step 6: Look up translations via GlobalDict. Save hits inline."""
        from scripts.esp_engine import needs_translation as _needs_trans
        gd = self._global_dict
        remaining = []
        dict_saved = 0
        for s in strings:
            if not _needs_trans(s["original"]):
                remaining.append(s)
                continue
            t = gd.get(s["original"])
            if t:
                self._string_mgr.save_string(
                    mod_name=mod_name, esp_name=s["esp"], key=s["key"],
                    translation=t, original=s.get("original", ""),
                    source="dict", job_id=job.id,
                )
                jm.add_string_update(job, s["key"], s["esp"], t, "translated", None, source="dict")
                dict_saved += 1
            else:
                remaining.append(s)
        if dict_saved:
            job.add_log(f"Reused {dict_saved} translations from global dict")
        return remaining, (dict_saved > 0)
