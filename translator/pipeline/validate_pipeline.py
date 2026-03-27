"""
ValidatePipeline — validate translated strings from SQLite.

Checks: token preservation, encoding artifacts, length limits,
        empty translations, null bytes, Skyrim inline tag preservation.
Saves results to {mod_name}_validation.json.
"""
from __future__ import annotations
import json
import logging
import re
import time
from pathlib import Path

log = logging.getLogger(__name__)

_LENGTH_LIMITS = {
    "FULL": 64, "SHRT": 32, "NNAM": 128, "DESC": 8000,
    "NAM1": 400, "ITXT": 60, "MNAM": 50, "FNAM": 50,
}


class ValidatePipeline:
    """Validates translated strings for a single mod."""

    def __init__(self, cfg, repo, stats_mgr=None):
        self._cfg       = cfg
        self._repo      = repo
        self._stats_mgr = stats_mgr

    def run(self, job, mod_name: str) -> None:
        from scripts.esp_engine import validate_tokens as _vt, quality_score as _qs
        from translator.web.job_manager import JobManager

        jm = JobManager.get()

        job.add_log(f"Validating translations for {mod_name}...")
        if self._repo is None:
            job.add_log("No database available")
            return

        rows = self._repo.get_all_strings(mod_name)
        esp_rows = [
            r for r in rows
            if not any(r["key"].startswith(p) for p in ("mcm:", "bsa-mcm:", "swf:"))
        ]

        issues: list[str] = []
        checked = 0

        for r in esp_rows:
            orig  = r.get("original", "") or ""
            trans = r.get("translation", "") or ""
            key   = r.get("key", "")
            field = r.get("field_type", "") or ""

            if not trans:
                continue
            checked += 1

            if "\x00" in trans:
                issues.append(f"NULL_BYTE: {key[:60]}")
            if re.search(r"[\x01-\x08\x0b\x0c\x0e-\x1f]", trans):
                issues.append(f"CTRL_CHAR: {key[:60]}")
            if any(art in trans for art in ("â€", "Ã©", "Ã ", "Â ")):
                issues.append(f"ENCODING_ARTIFACT: {key[:60]}")

            tok_ok, tok_issues = _vt(orig, trans)
            if not tok_ok:
                issues.append(f"TOKEN_MISMATCH [{'; '.join(tok_issues)}]: {key[:50]}")

            qs = _qs(orig, trans)
            if qs < 50:
                issues.append(f"LOW_QUALITY [qs={qs}]: {key[:50]}")

            limit = _LENGTH_LIMITS.get(field)
            if limit and len(trans) > limit:
                issues.append(f"TOO_LONG [{field}] {len(trans)}>{limit}: {key[:50]}")

        if issues:
            job.add_log(f"Found {len(issues)} issues in {checked} strings:")
            for iss in issues[:100]:
                job.add_log(f"  {iss}")
            if len(issues) > 100:
                job.add_log(f"  ... and {len(issues) - 100} more")
        else:
            job.add_log(f"Validation OK — {checked} strings checked, no issues found")

        jm.update_progress(job, 1, 1, f"{len(issues)} issues in {checked} strings")
        job.result = f"{len(issues)} validation issues"

        # Persist results to DB (primary) and JSON (legacy fallback for detail view)
        issues_count = len(issues)
        if self._stats_mgr:
            try:
                self._stats_mgr.save_validation_result(mod_name, issues_count)
                log.info("Validation result saved to DB for %s: %d issues", mod_name, issues_count)
            except Exception as exc:
                log.warning("Could not save validation result to DB: %s", exc)
        try:
            result_data = {
                "timestamp":    time.time(),
                "mod_name":     mod_name,
                "checked":      checked,
                "issues_count": issues_count,
                "issues":       issues[:200],
                "ok":           issues_count == 0,
            }
            out_path = self._cfg.paths.translation_cache.parent / f"{mod_name}_validation.json"
            out_path.write_text(
                json.dumps(result_data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            log.info("Validation results saved to %s", out_path.name)
        except Exception as exc:
            log.warning("Could not save validation results to JSON: %s", exc)
