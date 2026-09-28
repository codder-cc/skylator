"""Снимок выданного пакета: что именно ушло агенту, чтобы повтор исполнял то же самое.

Раньше мастер хранил только список строк назначения. Перераздача после падения агента
собирала контекст заново — текущим кодом, с текущими настройками, — и никто не мог
отличить «повторили тот же опыт» от «перевели ту же строку уже другим способом».

Снимок — пакет целиком, как он лёг в очередь: каждая строка со своей карточкой,
разговором, стилем, именами и аналогами, параметры вывода, память переводов, флаги
прохода. Плюс профиль: состав контекста, параметры и отпечаток кода, который собирал
контекст. Одинаковый профиль — один способ перевода; другой профиль — новая версия
задания, а не повтор.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS package_snapshots (
    assignment_id TEXT PRIMARY KEY,
    host_job_id   TEXT,
    worker_label  TEXT,
    profile_id    TEXT,
    profile_json  TEXT,
    n_strings     INTEGER,
    package_gz    BLOB,
    created_at    REAL
);
CREATE INDEX IF NOT EXISTS ix_package_snapshots_job ON package_snapshots(host_job_id);
"""

# Файлы, которые собирают контекст строки на мастере. Их отпечаток входит в профиль:
# изменили подбор аналогов или карточку — это уже другой способ перевода.
_HOST_SOURCES = (
    "translator/web/routes/jobs.py",
    "translator/web/offline_backend.py",
    "translator/validation/official_context.py",
    "translator/characters/cards.py",
    "translator/characters/speakers.py",
    "translator/characters/dialogue.py",
)
_ROOT = Path(__file__).resolve().parents[2]
# Ключи пакета, которые не меняют перевод: служебные номера и трасса.
_NOT_PROFILE = {"chunk_id", "offline_job_id", "host_job_id", "strings", "trace_full",
                "mod_name", "mods_context", "tm_pairs", "terminology", "type", "replay_of"}


def host_rev() -> str:
    h = hashlib.sha1()
    for rel in _HOST_SOURCES:
        try:
            h.update((_ROOT / rel).read_bytes())
        except OSError:
            h.update(rel.encode())
    return h.hexdigest()[:12]


def profile_of(package: dict, context_parts=None) -> dict:
    """Профиль пакета — всё, что определяет способ перевода, без самих строк."""
    prof = {k: v for k, v in package.items() if k not in _NOT_PROFILE}
    prof["context_parts"] = sorted(context_parts) if context_parts is not None else None
    prof["terminology_block"] = bool(package.get("terminology"))
    prof["tm"] = bool(package.get("tm_pairs"))
    prof["host_rev"] = host_rev()
    return prof


def profile_id(profile: dict) -> str:
    return hashlib.sha1(json.dumps(profile, sort_keys=True, ensure_ascii=False,
                                   default=str).encode("utf-8")).hexdigest()[:12]


def ensure(db) -> None:
    for stmt in SCHEMA.split(";"):
        if stmt.strip():
            db.execute(stmt)


def save(repo, package: dict, worker_label: str, context_parts=None,
         profile_from: str | None = None) -> str | None:
    """Записать снимок. Сбой записи не останавливает раздачу, но громко пишется в лог:
    пакет без снимка восстанавливается старым путём, пересборкой.

    `profile_from` — повтор: профиль берётся у исходного пакета, а не считается заново.
    Отпечаток кода с тех пор мог смениться, но исполняется-то старый пакет.
    """
    if repo is None:
        return None
    try:
        db = repo.db
        ensure(db)
        row = (db.execute("SELECT profile_json FROM package_snapshots WHERE assignment_id=?",
                          (profile_from,)).fetchone() if profile_from else None)
        prof = json.loads(row[0]) if row else profile_of(package, context_parts)
        pid = profile_id(prof)
        blob = gzip.compress(json.dumps(package, ensure_ascii=False).encode("utf-8"))
        db.execute(
            "INSERT OR REPLACE INTO package_snapshots (assignment_id, host_job_id, "
            "worker_label, profile_id, profile_json, n_strings, package_gz, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (package.get("offline_job_id"), package.get("host_job_id"), worker_label, pid,
             json.dumps(prof, ensure_ascii=False, default=str),
             len(package.get("strings") or []), blob, time.time()))
        db.commit()
        return pid
    except Exception as exc:                                       # noqa: BLE001
        log.error("snapshots: package %s not saved (%s) — its recovery will rebuild context",
                  package.get("offline_job_id"), exc)
        return None


def load(repo, assignment_id: str) -> dict | None:
    """Пакет назначения, каким он был выдан, или None."""
    try:
        ensure(repo.db)
        row = repo.db.execute(
            "SELECT package_gz FROM package_snapshots WHERE assignment_id=?",
            (assignment_id,)).fetchone()
    except Exception as exc:                                       # noqa: BLE001
        log.warning("snapshots: cannot read %s (%s)", assignment_id, exc)
        return None
    if not row:
        return None
    return json.loads(gzip.decompress(row[0]).decode("utf-8"))


def profile_for_job(repo, host_job_id: str) -> list[dict]:
    """Профили, с которыми было выдано задание: [{profile_id, profile, n_strings}]."""
    ensure(repo.db)
    return [{"profile_id": r[0], "profile": json.loads(r[1]), "n_strings": r[2]}
            for r in repo.db.execute(
                "SELECT profile_id, profile_json, SUM(n_strings) FROM package_snapshots "
                "WHERE host_job_id=? GROUP BY profile_id", (host_job_id,))]
