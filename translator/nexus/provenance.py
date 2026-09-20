"""Откуда взялась каждая чужая строка — мод, файл, версия, когда и по какому правилу.

`source='nexus-translation'` отвечает ровно на один вопрос: «это не наша работа». Этого
хватает, чтобы не спутать чужой перевод со своим, и не хватает больше ни на что:

    донор обновился — какие наши строки от него, и стоит ли перечитать;
    донор оказался плох — что именно он нам принёс, и как это откатить;
    поделиться собранным — без указания источника это чужая работа без имени автора;
    спор о строке — кто её написал, человек или машина, и когда.

Поэтому каждый перенос записывается целиком, а строка ссылается на него. Ссылка живёт в
`translated_by` (`nexus:<id>`), а не в новой колонке: поле и так дублировало `source`,
а таблица strings на полмиллиона записей — не то место, где стоит заводить колонку ради
сведений, которых у большинства строк нет.

Имя архива само по себе несёт номер мода, версию и отметку времени загрузки —
`...RUSSIAN TRANSLATION-37214-4-4-1591979229.7z`, — поэтому оно хранится как есть:
восстановить по нему можно больше, чем по любому разобранному полю.
"""
from __future__ import annotations

import json
import logging
import time

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS donor_imports (
    import_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    mod_name        TEXT NOT NULL,
    donor_mod_id    INTEGER,
    donor_name      TEXT,
    donor_file      TEXT,
    archive_name    TEXT,
    archive_bytes   INTEGER,
    language        TEXT,
    policy          TEXT,
    status_written  TEXT,
    donor_total     INTEGER,
    our_total       INTEGER,
    counts_json     TEXT,
    applied         INTEGER,
    confirmed       INTEGER,
    created_at      REAL
);
CREATE INDEX IF NOT EXISTS idx_donor_imports_mod  ON donor_imports(mod_name);
CREATE INDEX IF NOT EXISTS idx_donor_imports_mid  ON donor_imports(donor_mod_id);
"""


def ensure_schema(db) -> None:
    for stmt in SCHEMA.strip().split(";"):
        if stmt.strip():
            db.execute(stmt)


def record_import(db, report, result: dict, policy: str, status: str) -> int:
    """Записать перенос и вернуть его номер. Ошибка здесь не должна ронять перенос:
    строки уже в хранилище, и потерять сведения о них хуже, чем не записать их."""
    ensure_schema(db)
    plan = getattr(report, "plan", None)
    counts = {}
    donor_total = our_total = 0
    if plan is not None:
        try:
            counts = plan.counts
            donor_total, our_total = plan.donor_total, plan.our_total
        except Exception:
            pass
    archive = getattr(report, "archive", "") or ""
    cur = db.execute(
        "INSERT INTO donor_imports (mod_name, donor_mod_id, donor_name, donor_file, "
        "archive_name, archive_bytes, language, policy, status_written, donor_total, "
        "our_total, counts_json, applied, confirmed, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (getattr(report, "mod_name", ""), getattr(report, "donor_mod_id", None),
         getattr(report, "donor_name", ""), getattr(report, "donor_file", "") or "",
         archive.rsplit("\\", 1)[-1].rsplit("/", 1)[-1],
         getattr(report, "archive_bytes", 0) or 0,
         getattr(report, "language", ""), policy, status,
         donor_total, our_total, json.dumps(counts, ensure_ascii=False),
         int(result.get("applied") or 0), int(result.get("confirmed_by_official") or 0),
         time.time()))
    db.commit()
    return int(cur.lastrowid)


def stamp() -> str:
    """Метка, которую несёт строка, пока номер переноса ещё не известен."""
    return "nexus"


def mark(import_id: int) -> str:
    """`translated_by` строки, пришедшей этим переносом."""
    return f"nexus:{import_id}"


def import_of(db, string_id: int) -> dict | None:
    """Всё, что известно о происхождении одной строки."""
    row = db.execute("SELECT translated_by FROM strings WHERE id=?", (string_id,)).fetchone()
    if not row:
        return None
    tag = (row["translated_by"] if hasattr(row, "keys") else row[0]) or ""
    if not tag.startswith("nexus:"):
        return None
    try:
        iid = int(tag.split(":", 1)[1])
    except ValueError:
        return None
    ensure_schema(db)
    got = db.execute("SELECT * FROM donor_imports WHERE import_id=?", (iid,)).fetchone()
    return dict(got) if got else None


def summary(db) -> list:
    """Все переносы, свежие первыми — для отчёта и для того, чтобы поделиться."""
    ensure_schema(db)
    return [dict(r) for r in db.execute(
        "SELECT * FROM donor_imports ORDER BY created_at DESC")]
