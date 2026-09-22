"""
StringManager — single write gate for all string mutations.

All writes to the `strings` table must go through save_string().
This fixes:
  - TOCTOU race in bootstrap_esp() (esp_exists + bulk_insert inside one lock)
  - eval() → ast.literal_eval() for key parsing
  - original="" for MCM/BSA/SWF (callers must pass original)
  - Three writes (strings, string_history, job_strings) in one lock
"""
from __future__ import annotations
import ast
import hashlib
import json
import ast
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Module-level write lock — shared with repo._write_lock conceptually, but
# StringManager manages its own critical sections that span multiple tables.
_write_lock = threading.Lock()


@dataclass
class SaveResult:
    quality_score: Optional[int]
    status: str
    string_id: int
    was_inserted: bool
    # Текст, который РЕАЛЬНО лёг в строку. Не то же, что прислал вызывающий: ворота
    # могли оставить хранимый перевод или подставить официальный. Без этого поля
    # вызывающий не знал исхода и разносил по двойникам то, что ворота только что
    # отвергли, — так десять строк вернулись к английскому источнику за один проход.
    translation: str = ""


def _sha256_hash(text: str) -> str:
    """SHA256[:32] of text (16 bytes, negligible collision rate at ~2M strings)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


import re as _re
_WS_RE = _re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Conservative normalization for fuzzy reuse: collapse whitespace + casefold ONLY.
    (We deliberately do NOT strip punctuation — that would change meaning, e.g. a trailing
    period belongs in the translation.) Safe to reuse a translation across case/whitespace
    variants like '  Use ' / 'use' / 'USE'."""
    return _WS_RE.sub(" ", (text or "").strip()).casefold()


def _norm_hash(text: str) -> str | None:
    return _sha256_hash(normalize_text(text)) if text else None


def _identity_from_key(key: str):
    """(form_id, rec_type, field_type) из ключа строки, или None.

    Ключ плагинной строки — это str() кортежа опознания, и разобрать его дешевле, чем
    спрашивать базу. Нужно это потому, что тип записи доходит до ворот не всегда:
    агент возвращает текст, ключ и своё мнение о качестве, а `rec_type` в его ответе
    нет вовсе. Все правила, которые на тип смотрят, при этом молча стоят — за смену
    мастера правило рода не сработало НИ РАЗУ при 182 592 репликах в корпусе, и не
    пожаловалось, потому что «тип не INFO» выглядит как законный отказ.

    Ключи MCM/SWF кортежами не являются — для них молчим.
    """
    if not key or not key.startswith("("):
        return None
    try:
        parsed = ast.literal_eval(key)
    except (ValueError, SyntaxError):
        return None
    if not isinstance(parsed, tuple) or len(parsed) < 3:
        return None
    return (parsed[0] or None, parsed[1] or None, parsed[2] or None)


class StringManager:
    """Single write gate for all string mutations."""

    def __init__(self, repo, mods_dir: Path):
        """
        Args:
            repo: StringRepo instance
            mods_dir: Path to the mods directory (for ESP bootstrap)
        """
        self._repo = repo
        self._mods_dir = Path(mods_dir)
        self._ledger = None   # lazy WorkLedger (strangler #1 dual-write)
        self._terms = None    # lazy glossary, see _glossary()

    def _glossary(self) -> dict:
        """The curated EN→RU glossary, loaded once.

        It is injected into every prompt and, until now, verified nowhere. On the live
        collection that let 352 strings through in which "Skyrim" had become "Сиродил" —
        a different province — every one of them recorded as translated. Enforcing it here,
        in the single write gate, means no delivery path can skip the check.
        """
        if self._terms is None:
            self._terms = {}
            try:
                from translator.config import get_config
                from translator.validation.terminology import load_terms
                self._terms = load_terms(get_config().paths.skyrim_terms)
            except Exception as exc:
                log.warning("glossary: could not load terms, enforcement is off: %s", exc)
        return self._terms

    # ── Main write entry point ───────────────────────────────────────────────

    def save_string(
        self,
        mod_name: str,
        esp_name: str,
        key: str,
        translation: str,
        original: str = "",
        source: str = "ai",
        machine_label: str = "",
        job_id: str = "",
        quality_score: Optional[int] = None,
        status: Optional[str] = None,
        produced_at: Optional[float] = None,
        merge: bool = False,
        prefer_incoming: bool = False,
        rec_type: Optional[str] = None,
        field_type: Optional[str] = None,
        form_id: Optional[str] = None,
    ) -> SaveResult:
        """Single write entry point for ALL string types.

        - Computes quality_score if not provided (skips if original is empty)
        - Computes string_hash = SHA256(original)[:32]
        - All three writes inside one _write_lock acquire:
            1. strings UPSERT
            2. string_history INSERT
            3. job_strings UPDATE (if job_id provided)

        merge=True — keep whichever translation is better instead of overwriting.
        Set it for deliveries from agents, where arrival order is not controlled: an
        agent presumed dead can come back a week later with work that was meanwhile
        reassigned, and both deliveries are legitimate. Leave it False for manual
        edits and resets, which must win unconditionally.
        """
        from translator.validation.quality import compute_string_status

        # Опознание строки — до всякого суждения: и оценка качества, и правила ниже
        # смотрят на тип записи, а вызывающий передаёт его не всегда.
        if rec_type is None or field_type is None or form_id is None:
            _ident = _identity_from_key(key)
            if _ident:
                form_id = form_id or _ident[0]
                rec_type = rec_type or _ident[1]
                field_type = field_type or _ident[2]

        # Compute quality score / status outside the lock (CPU-only)
        computed_qs = quality_score
        computed_status = status

        if translation:
            if original:
                # Judged here, from the text, whatever the caller claimed. The caller is
                # usually an agent, which computes its own status from its own copy of
                # the rules — an older copy, without the glossary, and it arrives
                # asserting "translated".
                #
                # This block used to keep the caller's status whenever one was supplied
                # and only re-check markup and the glossary on top. Every other rule was
                # therefore skipped at the write gate: echo, identifiers, numbers,
                # foreign script, model commentary, repeated words, markdown, prompt
                # scaffolding, runaway repetition. They took effect only when a recompute
                # happened to run afterwards, which is why each pass left damage the
                # recompute found later — 265 strings reading «Dragonbone Mace ⇥ Кистен
                # из Драконьей Кости» sat accepted with the echo rule live and refusing
                # them on demand.
                #
                # One judgement, one place. A caller's status is not evidence.
                computed_qs, _tok, _issues, computed_status = compute_string_status(
                    original, translation, self._glossary(), rec_type, field_type)
            else:
                # MCM/BSA/SWF have no original to judge against; a translation is all the
                # evidence there is.
                if computed_status is None:
                    computed_status = "translated"
        else:
            computed_qs = None
            computed_status = "pending"

        # ── Официальная локализация: авторитет выше машинного текста ────────────
        # Раньше выравнивание по официальной таблице было скриптом, который проходил по
        # корпусу разом. Скрипт — это снимок: всё, что агенты доставят после него, снимок
        # не видит. «Fort Dawnguard» был верен с марта, подтверждён выравниванием в 13:46
        # и затёрт доставкой агента в 13:52. За один день так разошлись 410 строк.
        # Правило стоит здесь, потому что здесь — единственный путь записи.
        if translation and original:
            try:
                from translator.validation.authority import load_official, official_override
                _table = load_official()
                _official = None
                if original.strip() in _table:
                    # FormID решает, переопределяет ли запись ванильную, а вызывающие его
                    # не передают. Один индексный SELECT — но только для тех источников,
                    # что вообще есть в таблице, то есть для доли процента записей.
                    _fid = form_id
                    if _fid is None:
                        _row = self._repo.db.execute(
                            "SELECT form_id FROM strings WHERE mod_name=? AND esp_name=? "
                            "AND key=?", (mod_name, esp_name, key)).fetchone()
                        _fid = _row["form_id"] if _row else None
                    _official = official_override(original, translation, _fid, source, _table)
            except Exception as exc:
                log.warning("authority: правило не отработало для %s/%s: %s", mod_name, key, exc)
                _official = None
            if _official:
                log.info("authority: %s/%s — официальное %r вместо %r",
                         mod_name, key, _official[:40], translation.strip()[:40])
                translation = _official
                computed_qs, _tok, _issues, computed_status = compute_string_status(
                    original, translation, self._glossary(), rec_type, field_type)
                source = "vanilla"
                merge = False          # таблица не соревнуется с хранимым текстом

        # ── Род говорящего: та же причина, что и у таблицы выше ─────────────────
        # Элдавин — женщина, и система это знает точно: `EldawynVoice`, флаг пола в
        # записи NPC_. Её реплики всё равно звучали мужским родом, и история одной строки
        # объясняет почему:
        #
        #   20 сен 13:47  [gender:voice]  правка рода — верно
        #   21 сен 00:10  [ai]            ночной слепой прогон вернул мужской
        #   21 сен 11:16  [duplicate]     разнос по двойникам добил
        #
        # Скрипт чинил симптом, а всё, что писалось после, ломало обратно — ровно как с
        # официальной таблицей. Снимок не видит доставленного после него, поэтому
        # суждение переносится в момент записи. На 23 сломанных строках Элдавин правило
        # чинит 23.
        if translation and original and rec_type == "INFO":
            try:
                from translator.characters import gender as _g
                from translator.characters import speakers as _sp
                _fid = form_id
                if _fid is None:
                    _row = self._repo.db.execute(
                        "SELECT form_id FROM strings WHERE mod_name=? AND esp_name=? "
                        "AND key=?", (mod_name, esp_name, key)).fetchone()
                    _fid = _row["form_id"] if _row else None
                _sex = _sp.gender_for(esp_name, _fid)
                if _sex:
                    _fixed = _g.enforce(original, translation, _sex)
                    if _fixed != translation:
                        log.info("gender: %s/%s — %s род говорящего", mod_name, key,
                                 "женский" if _sex == "f" else "мужской")
                        translation = _fixed
                        computed_qs, _tok, _issues, computed_status = compute_string_status(
                            original, translation, self._glossary(), rec_type, field_type)
            except Exception as exc:                                   # noqa: BLE001
                log.warning("gender: правило не отработало для %s/%s: %s",
                            mod_name, key, exc)

        # ── Род СОБЕСЕДНИКА: реплики игрока обращены к персонажу ───────────────
        # «Ты грубиян» — это реплика ИГРОКА, обращённая к Элдавин, и род здесь
        # принадлежит ей. Пол говорящего к этому отношения не имеет, и правило выше
        # такие строки не трогает вовсе: они не INFO/NAM1.
        #
        # Кто собеседник — видно из графа диалогов: у темы отвечает тот, чьи ответы
        # лежат в её группе. Замер: адресат известен у 64,3% реплик игрока, и в 485
        # из них род стоял неверно.
        if translation and original and rec_type in ("DIAL", "INFO"):
            try:
                from translator.characters import dialogue as _dlg
                from translator.characters import gender as _g
                _fid = form_id
                if _fid is None:
                    _row = self._repo.db.execute(
                        "SELECT form_id FROM strings WHERE mod_name=? AND esp_name=? "
                        "AND key=?", (mod_name, esp_name, key)).fetchone()
                    _fid = _row["form_id"] if _row else None
                _to = _dlg.addressee_gender_for(esp_name, _fid, rec_type, field_type)
                if _to:
                    _fixed = _g.enforce_addressee(original, translation, _to)
                    if _fixed != translation:
                        log.info("addressee: %s/%s — %s род собеседника", mod_name, key,
                                 "женский" if _to == "f" else "мужской")
                        translation = _fixed
                        computed_qs, _tok, _issues, computed_status = compute_string_status(
                            original, translation, self._glossary(), rec_type, field_type)
            except Exception as exc:                                   # noqa: BLE001
                log.warning("addressee: правило не отработало для %s/%s: %s",
                            mod_name, key, exc)

        # ── Merge against what is already stored (agent deliveries only) ────────
        if merge and translation:
            existing = self._repo.db.execute(
                "SELECT id, translation, quality_score, status FROM strings "
                "WHERE mod_name=? AND esp_name=? AND key=?",
                (mod_name, esp_name, key),
            ).fetchone()
            prev = (existing["translation"] or "").strip() if existing else ""
            if prev and prev != translation.strip():
                from translator.validation.quality import pick_better
                # A review delivery was made with the stored text in hand and told to
                # change it only when it is wrong, so on an equal score its answer is the
                # later and better-informed one. Without this the pass cannot land a
                # single meaning fix: the score cannot tell a forge from an anvil, both
                # sides read 100, and the stored text keeps winning.
                best = pick_better(original, prev, translation,
                                   prefer_b_on_tie=prefer_incoming)
                if best["chose"] == "a":
                    # What we already have wins. Leave the row untouched and report it, so a
                    # late delivery from a returning agent cannot undo better work.
                    log.info("merge: kept stored translation for %s/%s — incoming from %s scored lower",
                             mod_name, key, machine_label or source)
                    return SaveResult(
                        quality_score = existing["quality_score"],
                        status        = existing["status"] or "translated",
                        string_id     = existing["id"],
                        was_inserted  = False,
                        translation   = prev,
                    )
                translation     = best["translation"]
                computed_qs     = best["quality_score"]
                computed_status = best["status"]

        string_hash = _sha256_hash(original) if original else None
        norm_hash = _norm_hash(original)
        # When the translation was MADE, not when this process heard about it. The master
        # is run intermittently by design — switched on to look at the run, off again —
        # and the agents translate the whole time it is away. Stamping arrival meant a
        # night of work landed as thousands of rows sharing one second, so "what did that
        # machine do while I was asleep" had no answer in the data at all.
        translated_at = (produced_at or time.time()) if translation else None

        with _write_lock:
            # 1. strings UPSERT (does its own commit, but we're inside the lock)
            sql_upsert = """
            INSERT INTO strings
                (mod_name, esp_name, key, original, translation, status,
                 quality_score, updated_at, source, translated_by, translated_at,
                 string_hash, norm_hash)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(mod_name, esp_name, key) DO UPDATE SET
                translation   = excluded.translation,
                status        = excluded.status,
                quality_score = excluded.quality_score,
                updated_at    = excluded.updated_at,
                source        = COALESCE(excluded.source, source),
                translated_by = COALESCE(excluded.translated_by, translated_by),
                translated_at = COALESCE(excluded.translated_at, translated_at),
                string_hash   = COALESCE(excluded.string_hash, string_hash),
                norm_hash     = COALESCE(excluded.norm_hash, norm_hash)
            """
            self._repo.db.execute(sql_upsert, (
                mod_name, esp_name, key, original, translation,
                computed_status or "pending", computed_qs,
                time.time(), source,
                machine_label or None, translated_at, string_hash, norm_hash,
            ))

            # Fetch id for history
            row = self._repo.db.execute(
                "SELECT id FROM strings WHERE mod_name=? AND esp_name=? AND key=?",
                (mod_name, esp_name, key),
            ).fetchone()
            string_id = row["id"] if row else None

            if string_id is not None:
                # 2. string_history INSERT
                self._repo.db.execute("""
                    INSERT INTO string_history
                        (string_id, translation, status, quality_score, source, machine_label, job_id)
                    VALUES (?,?,?,?,?,?,?)
                """, (
                    string_id, translation, computed_status or "pending",
                    computed_qs, source,
                    machine_label or None, job_id or None,
                ))

                # 3. job_strings UPDATE (if job_id provided)
                if job_id:
                    self._repo.db.execute("""
                        INSERT INTO job_strings (job_id, string_id, status)
                        VALUES (?,?,'done')
                        ON CONFLICT(job_id, string_id) DO UPDATE SET status='done'
                    """, (job_id, string_id))

            self._repo.db.commit()

        # Dual-write to the work ledger (strangler #1): shadow the completed translation as an
        # append-only event carrying the source-text hash → cross-mod dedup + progress
        # projections read from ONE log. Best-effort: never let it affect the real save.
        if translation and (computed_status or "") in ("translated", "needs_review"):
            try:
                self._ledger_write(mod_name, esp_name, key, original, translation,
                                   machine_label or source, job_id)
            except Exception:
                pass

        return SaveResult(
            quality_score=computed_qs,
            status=computed_status or "pending",
            string_id=string_id or 0,
            was_inserted=string_id is not None,
            translation=translation or "",
        )

    def _ledger_write(self, mod_name, esp_name, key, original, translation, agent, job_id):
        if self._ledger is None:
            from translator.jobs.work_ledger import WorkLedger
            self._ledger = WorkLedger(self._repo.db)
        from translator.jobs.work_ledger import content_hash as _lhash, RESULT
        self._ledger.append(
            f"{mod_name}::{esp_name}::{key}", RESULT,
            agent_id=(agent or None), job_id=(job_id or None),
            content_hash=_lhash(original) if original else None,
            payload={"translation": translation},
        )

    # ── ESP bootstrap ────────────────────────────────────────────────────────

    def bootstrap_esp(self, mod_name: str, esp_name: str) -> int:
        """Seed SQLite from ESP binary if not yet seeded.
        TOCTOU-safe: esp_exists() check AND bulk_insert inside one _write_lock.
        Returns number of rows inserted.
        """
        from scripts.esp_engine import extract_all_strings

        with _write_lock:
            if self._repo.esp_exists(mod_name, esp_name):
                return 0

            esp_stem = Path(esp_name).stem
            mod_dir = self._mods_dir / mod_name
            candidates = (
                list(mod_dir.rglob(f"{esp_stem}.esp"))
                + list(mod_dir.rglob(f"{esp_stem}.esm"))
                + list(mod_dir.rglob(f"{esp_stem}.esl"))
            )
            if not candidates:
                log.warning("bootstrap_esp: ESP not found for %s / %s", mod_name, esp_name)
                return 0

            strings, _ = extract_all_strings(candidates[0])
            count = self._repo.bulk_insert_strings(mod_name, esp_name, strings)
            log.info("bootstrap_esp: seeded %s / %s (%d strings)", mod_name, esp_name, count)
            return count

    # ── Bulk status helpers ──────────────────────────────────────────────────

    def mark_untranslatable(self, mod_name: str) -> int:
        """Set translation=original, source='untranslatable', quality_score=100
        for all strings where needs_translation(original)==False.
        Returns number of strings updated.
        """
        from translator.validation.quality import needs_translation

        rows = self._repo.get_all_strings(mod_name)
        count = 0
        for s in rows:
            orig = s.get("original", "")
            if not needs_translation(orig) and s.get("status") != "translated":
                self.save_string(
                    mod_name=mod_name,
                    esp_name=s["esp_name"],
                    key=s["key"],
                    translation=orig,
                    original=orig,
                    source="untranslatable",
                    quality_score=100,
                    status="translated",
                )
                count += 1
        return count

    def reset_to_pending(self, mod_name: str, esp_name: Optional[str] = None) -> int:
        """Clear translations, set status='pending', source='pending'.
        Returns number of strings reset.
        """
        with _write_lock:
            if esp_name:
                self._repo.db.execute("""
                    UPDATE strings SET
                        translation='', status='pending', quality_score=NULL,
                        source='pending', translated_by=NULL, translated_at=NULL,
                        updated_at=unixepoch('now','subsec')
                    WHERE mod_name=? AND esp_name=?
                """, (mod_name, esp_name))
            else:
                self._repo.db.execute("""
                    UPDATE strings SET
                        translation='', status='pending', quality_score=NULL,
                        source='pending', translated_by=NULL, translated_at=NULL,
                        updated_at=unixepoch('now','subsec')
                    WHERE mod_name=?
                """, (mod_name,))
            count = self._repo.db.execute("SELECT changes()").fetchone()[0]
            self._repo.db.commit()
        return count

    def approve_string(self, string_id: int) -> None:
        """Set status='translated' for a needs_review string. Records history."""
        row = self._repo.get_string_by_id(string_id)
        if not row:
            log.warning("approve_string: string_id=%d not found", string_id)
            return

        with _write_lock:
            self._repo.db.execute("""
                UPDATE strings SET status='translated', updated_at=unixepoch('now','subsec')
                WHERE id=?
            """, (string_id,))
            self._repo.db.execute("""
                INSERT INTO string_history
                    (string_id, translation, status, quality_score, source, machine_label, job_id)
                VALUES (?,?,?,?,?,?,?)
            """, (
                string_id, row["translation"], "translated", row["quality_score"],
                "manual", None, None,
            ))
            self._repo.db.commit()
        log.debug("approved string_id=%d", string_id)
