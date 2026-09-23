"""Слой кандидатов: всё, что перевели машины, — записано, а не только принятое.

Прогон на пять суток показал, почему он нужен. Плотная модель 27B переводила худшие
типы записей, а ворота отбрасывали её ответы один за другим: ворота принимают чужой
текст, только если он строго лучше по оценке, а чистый перевод получает те же 100
баллов, что и хранимый. Ничья уходила хранимому. За ночь 14 тысяч ответов были
отвергнуты и потеряны: агент стирает доставленное, как только хост подтвердит приём.

Потеряна была не только работа, но и возможность что-либо о ней узнать — какой текст
предложила модель, чем он отличался, стал бы он лучше. Решение о том, применять ли
перевод, было склеено с фактом его получения, и ошибка в решении уничтожала данные.

ЗДЕСЬ ЭТО РАЗДЕЛЕНО

    получить     каждый ответ записывается сюда до всякого решения, со всем, что о
                 нём известно в эту минуту: что лежало в базе, что сказали правила,
                 какая машина и какая модель его дали;
    решить       ворота решают отдельно, и их решение пишется рядом, а не вместо;
    применить    — позже, отдельным шагом, по политике, которую можно проверить на
                 этих же данных до того, как тронуть корпус.

Флаг `candidate_layer_only` выключает третий шаг совсем: ответы копятся здесь, а
`strings` не меняется ни на строку. Так пятидневный прогон становится безопасным по
построению — худшее, что он может сделать, это записать сюда лишнее.
"""
from __future__ import annotations

import json
import logging
import time

log = logging.getLogger(__name__)

SETTING_LAYER_ONLY = "candidate_layer_only"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    string_id         INTEGER,
    mod_name          TEXT,
    esp_name          TEXT,
    key               TEXT,
    rec_type          TEXT,
    field_type        TEXT,
    original          TEXT,
    translation       TEXT,
    stored_at_arrival TEXT,      -- что лежало в strings, когда ответ пришёл
    same_as_stored    INTEGER,   -- 1, если ответ совпал с хранимым дословно
    machine           TEXT,
    model             TEXT,
    job_id            TEXT,
    produced_at       REAL,      -- когда агент его сделал
    received_at       REAL,      -- когда хост его получил
    score             INTEGER,   -- оценка правил для ЭТОГО текста
    rules_status      TEXT,      -- translated / needs_review — вердикт правил
    issues            TEXT,      -- JSON: что правила нашли
    gate              TEXT,      -- что сделали ворота: layer_only / accepted / kept_stored
    UNIQUE(string_id, machine, produced_at)
);
CREATE INDEX IF NOT EXISTS idx_cand_string ON candidates(string_id);
CREATE INDEX IF NOT EXISTS idx_cand_job    ON candidates(job_id);
CREATE INDEX IF NOT EXISTS idx_cand_model  ON candidates(model, rec_type);
"""

def ensure(db) -> None:
    """Создать таблицу, если её нет. Дёшево: один раз на объект базы.

    Отметка хранится на самом объекте, а не по `id(db)`: Python переиспользует id для
    нового объекта, как только старый собран, и тогда «таблица уже есть» относится к
    базе, в которой её нет. Тест с базой в памяти поймал это сразу.
    """
    if getattr(db, "_candidates_ready", False):
        return
    # По одному выражению: у обёртки TranslationDB есть execute, но нет executescript,
    # а соединения у неё свои на каждый поток — таблица же создаётся в файле базы и
    # видна всем, так что достаточно сделать это один раз.
    for stmt in (x.strip() for x in _SCHEMA.split(";")):
        if stmt:
            db.execute(stmt)
    db.commit()
    try:
        db._candidates_ready = True
    except Exception:                                              # noqa: BLE001
        pass


def layer_only(repo) -> bool:
    """Включён ли режим «только записывать, ничего не применять»."""
    if repo is None:
        return False
    try:
        return bool(repo.db.get_setting(SETTING_LAYER_ONLY, False))
    except Exception:                                              # noqa: BLE001
        return False


def set_layer_only(repo, on: bool) -> None:
    repo.db.set_setting(SETTING_LAYER_ONLY, bool(on))


def _identity(key: str) -> tuple[str | None, str | None]:
    from translator.data_manager.string_manager import _identity_from_key
    got = _identity_from_key(key)
    return (got[1], got[2]) if got else (None, None)


def record(repo, *, string_id, mod_name: str, esp_name: str, key: str,
           original: str, translation: str, machine: str, model: str,
           job_id: str, produced_at, terms=None) -> int | None:
    """Записать один ответ до всякого решения. Возвращает id кандидата или None.

    Вердикт правил считается здесь же и для ЭТОГО текста — тем же
    compute_string_status, что и на воротах, — чтобы потом можно было отделить
    «ворота отвергли порчу» от «ворота отвергли равноценное».
    """
    if repo is None or not translation:
        return None
    db = repo.db
    ensure(db)
    rec_type, field_type = _identity(key)
    stored = ""
    try:
        row = db.execute("SELECT id, translation FROM strings WHERE mod_name=? AND "
                         "esp_name=? AND key=?", (mod_name, esp_name, key)).fetchone()
        if row:
            stored = row["translation"] or ""
            if string_id is None:
                string_id = row["id"]
    except Exception:                                              # noqa: BLE001
        pass
    score = status = None
    issues: list = []
    try:
        from translator.validation.quality import compute_string_status
        score, _tok, issues, status = compute_string_status(
            original, translation, terms, rec_type, field_type)
    except Exception as exc:                                       # noqa: BLE001
        log.debug("candidates: rules failed for %s: %s", key, exc)
    try:
        cur = db.execute(
            "INSERT OR IGNORE INTO candidates (string_id, mod_name, esp_name, key, "
            "rec_type, field_type, original, translation, stored_at_arrival, "
            "same_as_stored, machine, model, job_id, produced_at, received_at, score, "
            "rules_status, issues, gate) VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (string_id, mod_name, esp_name, key, rec_type, field_type, original,
             translation, stored, int(translation.strip() == stored.strip()),
             machine, model, job_id, produced_at, time.time(), score, status,
             json.dumps(issues or [], ensure_ascii=False), None))
        db.commit()
        return cur.lastrowid or None
    except Exception as exc:                                       # noqa: BLE001
        log.warning("candidates: could not record %s/%s: %s", mod_name, key, exc)
        return None


def set_gate(repo, cand_id: int | None, gate: str) -> None:
    """Дописать, что сделали ворота. Рядом с ответом, а не вместо него."""
    if repo is None or not cand_id:
        return
    try:
        repo.db.execute("UPDATE candidates SET gate=? WHERE id=?", (gate, cand_id))
        repo.db.commit()
    except Exception as exc:                                       # noqa: BLE001
        log.debug("candidates: gate not recorded for %s: %s", cand_id, exc)
