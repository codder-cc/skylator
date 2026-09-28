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
    judge             TEXT,      -- вердикт судьи на агенте: fresh / stored / unsure / same
    rival             TEXT,      -- с чем судья сравнивал (хранимое на момент раздачи)
    finish_reason     TEXT,      -- почему модель остановилась: stop / length
    -- Трасса вызова, давшего ответ (агент, agent_traces). Без неё эксперимент не мог
    -- доказать, что видела модель: лог «карточка приложена» — не доказательство.
    trace_kind        TEXT,      -- translate / retry / candidate
    prompt_sha        TEXT,      -- sha256 промпта, всегда
    prompt            TEXT,      -- промпт целиком, только из пакета с trace_full
    params_json       TEXT,      -- что фактически получил mlx_lm (sampler, max_tokens…)
    tokens_in         INTEGER,
    tokens_out        INTEGER,
    seconds           REAL,
    code_rev          TEXT,      -- git HEAD агента
    judge_trace_json  TEXT,      -- JSON: трассы двух вызовов судьи
    raw_output        TEXT,      -- сырой ответ модели, только при trace_full
    UNIQUE(string_id, machine, produced_at)
);
CREATE INDEX IF NOT EXISTS idx_cand_string ON candidates(string_id);
CREATE INDEX IF NOT EXISTS idx_cand_job    ON candidates(job_id);
CREATE INDEX IF NOT EXISTS idx_cand_model  ON candidates(model, rec_type);
CREATE TABLE IF NOT EXISTS candidate_jobs (
    assignment_id TEXT PRIMARY KEY,
    job_id        TEXT
);
"""

# Колонки, которых не было в первой версии таблицы, — в порядке появления.
_ADDED_COLUMNS = (
    ("judge", "TEXT"), ("rival", "TEXT"), ("finish_reason", "TEXT"),
    ("trace_kind", "TEXT"), ("prompt_sha", "TEXT"), ("prompt", "TEXT"),
    ("params_json", "TEXT"), ("tokens_in", "INTEGER"), ("tokens_out", "INTEGER"),
    ("seconds", "REAL"), ("code_rev", "TEXT"), ("judge_trace_json", "TEXT"),
    ("raw_output", "TEXT"),
    # Почему ответ применён или удержан: причина, версия политики, checkpoint.
    ("decision_json", "TEXT"),
)


def _trace_fields(trace, judge_trace) -> tuple:
    """Трасса из доставки → значения колонок. Кривая трасса — пустые колонки, не сбой.

    Доставку от старого агента (без трасс) и мусор в поле нельзя превращать в отказ
    записи: отказ здесь значит «не подтверждать приём», и агент слал бы строку вечно.
    """
    t = trace if isinstance(trace, dict) else {}

    def num(v, cast):
        try:
            return cast(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    params = t.get("params")
    jt = [x for x in judge_trace if isinstance(x, dict)] if isinstance(judge_trace, list) else []
    return (
        (t.get("kind") or None),
        (t.get("prompt_sha") or None),
        (t.get("prompt") if isinstance(t.get("prompt"), str) else None),
        (json.dumps(params, ensure_ascii=False, sort_keys=True) if params is not None else None),
        num(t.get("tokens_in"), int),
        num(t.get("tokens_out"), int),
        num(t.get("seconds"), float),
        (t.get("code_rev") or None),
        (json.dumps(jt, ensure_ascii=False) if jt else None),
        (t.get("output") if isinstance(t.get("output"), str) else None),
    )


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
    # Таблица, созданная до появления судьи и трасс, получает их колонки на месте.
    have = {r[1] for r in db.execute("PRAGMA table_info(candidates)").fetchall()}
    for col, kind in _ADDED_COLUMNS:
        if col not in have:
            db.execute(f"ALTER TABLE candidates ADD COLUMN {col} {kind}")
    db.commit()
    try:
        db._candidates_ready = True
    except Exception:                                              # noqa: BLE001
        pass


def layer_only(repo) -> bool:
    """Включён ли режим «только записывать, ничего не применять».

    Не прочиталась настройка — значит «да». Раньше ошибка чтения давала «нет», то
    есть сбой базы включал применение в корпус: ровно наоборот тому, ради чего режим
    заведён.
    """
    if repo is None:
        return False
    try:
        return bool(repo.db.get_setting(SETTING_LAYER_ONLY, False))
    except Exception as exc:                                       # noqa: BLE001
        log.warning("candidates: layer_only unreadable (%s) — staying in layer-only", exc)
        return True


def remember_job(repo, assignment_id: str | None, job_id: str | None) -> None:
    """Запомнить, какому заданию принадлежит назначение агента.

    Отправка агента знает оба номера, сверка — только назначение. Раньше отправка
    писала в job_id номер задания, а сверка — номер назначения, и разметка правок
    имён по заданию теряла всё, что пришло сверкой.
    """
    if repo is None or not assignment_id or not job_id:
        return
    try:
        ensure(repo.db)
        repo.db.execute("INSERT OR REPLACE INTO candidate_jobs (assignment_id, job_id) "
                        "VALUES (?,?)", (assignment_id, job_id))
        repo.db.commit()
    except Exception as exc:                                       # noqa: BLE001
        log.debug("candidates: job link not stored: %s", exc)


def job_for(repo, assignment_id: str | None) -> str:
    """Номер задания для назначения; если связь неизвестна — само назначение."""
    if repo is None or not assignment_id:
        return assignment_id or ""
    try:
        ensure(repo.db)
        row = repo.db.execute("SELECT job_id FROM candidate_jobs WHERE assignment_id=?",
                              (assignment_id,)).fetchone()
        if row and row[0]:
            return row[0]
        row = repo.db.execute("SELECT job_id FROM assignments WHERE assignment_id=?",
                              (assignment_id,)).fetchone()
        if row and row[0]:
            return row[0]
    except Exception:                                              # noqa: BLE001
        pass
    return assignment_id


def set_layer_only(repo, on: bool) -> None:
    repo.db.set_setting(SETTING_LAYER_ONLY, bool(on))


def _identity(key: str) -> tuple[str | None, str | None]:
    from translator.data_manager.string_manager import _identity_from_key
    got = _identity_from_key(key)
    return (got[1], got[2]) if got else (None, None)


def record(repo, *, string_id, mod_name: str, esp_name: str, key: str,
           original: str, translation: str, machine: str, model: str,
           job_id: str, produced_at, terms=None, judge: str | None = None,
           rival: str | None = None, finish_reason: str | None = None,
           trace: dict | None = None, judge_trace: list | None = None) -> int | None:
    """Записать один ответ до всякого решения. Возвращает id кандидата или None.

    Вердикт правил считается здесь же и для ЭТОГО текста — тем же
    compute_string_status, что и на воротах, — чтобы потом можно было отделить
    «ворота отвергли порчу» от «ворота отвергли равноценное».

    `trace` и `judge_trace` — что агент записал о вызовах модели (см. agent_traces):
    хэш и параметры всегда, промпт — только из экспериментального пакета.
    """
    if repo is None or not translation:
        return None
    db = repo.db
    ensure(db)
    rec_type, field_type = _identity(key)
    stored = ""
    stored_original = None
    try:
        row = db.execute("SELECT id, translation, original FROM strings WHERE mod_name=? "
                         "AND esp_name=? AND key=?", (mod_name, esp_name, key)).fetchone()
        if row:
            stored = row["translation"] or ""
            stored_original = row["original"]
            if string_id is None:
                string_id = row["id"]
    except Exception:                                              # noqa: BLE001
        pass
    score = status = None
    issues: list = []
    # Без глоссария правила слепы к именам: «Утёс» вместо Вайтрана и «Бринджольф»
    # проходили как чистые, а именно на именах новый перевод чаще всего хуже старого.
    if terms is None:
        try:
            from translator.validation.terminology import load_terms
            terms = load_terms()
        except Exception:                                          # noqa: BLE001
            terms = None
    try:
        from translator.validation.quality import compute_string_status
        score, _tok, issues, status = compute_string_status(
            original, translation, terms, rec_type, field_type)
    except Exception as exc:                                       # noqa: BLE001
        log.debug("candidates: rules failed for %s: %s", key, exc)
    # Два факта, которые правила по тексту не видят и не должны пересчитывать заново.
    # Модель упёрлась в лимит — ответ оборван, как бы чисто ни выглядел остаток; раньше
    # агент ставил needs_review у себя, а здесь текст пересчитывался и становился
    # translated. Исходник ответа не совпадает с тем, что сейчас лежит в строке, —
    # ответ на другой текст (строку правили или ключ сдвинулся), применять его нельзя.
    if (finish_reason or "") == "length":
        status = "needs_review"
        issues = list(issues or []) + [{"type": "generation_limit",
                                        "message": "generation limit reached: answer cut off"}]
    if (finish_reason or "") == "format":
        # Ответ пришёл не в том виде (лишние пункты): текст мог потерять часть.
        status = "needs_review"
        issues = list(issues or []) + [{"type": "output_format",
                                        "message": "answer split into extra items: text may be lost"}]
    if stored_original is not None and (stored_original or "").strip() != (original or "").strip():
        status = "needs_review"
        issues = list(issues or []) + [{"type": "source_mismatch",
                                        "message": "source text differs from the corpus row"}]
    try:
        cur = db.execute(
            "INSERT OR IGNORE INTO candidates (string_id, mod_name, esp_name, key, "
            "rec_type, field_type, original, translation, stored_at_arrival, "
            "same_as_stored, machine, model, job_id, produced_at, received_at, score, "
            "rules_status, issues, gate, judge, rival, finish_reason, trace_kind, "
            "prompt_sha, prompt, params_json, tokens_in, tokens_out, seconds, code_rev, "
            "judge_trace_json, raw_output) VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (string_id, mod_name, esp_name, key, rec_type, field_type, original,
             translation, stored, int(translation.strip() == stored.strip()),
             machine, model, job_id, produced_at, time.time(), score, status,
             json.dumps(issues or [], ensure_ascii=False), None, judge or None,
             rival or None, finish_reason or None) + _trace_fields(trace, judge_trace))
        db.commit()
        if cur.rowcount:
            return cur.lastrowid
        # Этот ответ уже записан (повторная доставка): вернуть его id. None остаётся
        # только за настоящим сбоем — и по нему хост НЕ подтверждает приём.
        got = db.execute("SELECT id FROM candidates WHERE string_id IS ? AND machine=? "
                         "AND produced_at IS ?", (string_id, machine, produced_at)).fetchone()
        return (got[0] if got else None)
    except Exception as exc:                                       # noqa: BLE001
        log.warning("candidates: could not record %s/%s: %s", mod_name, key, exc)
        return None


def judge_forbids(judge: str | None) -> bool:
    """Судья видел оба текста и не выбрал новый — применять его нельзя.

    Пустой вердикт (судьи не было) ничего не запрещает: тогда решают ворота, как раньше.
    """
    # «invalid» — ответ судьи не читается как A или B. Это не «судьи не было», а
    # «судья не решил»: пропускать новый текст по нему нельзя.
    return (judge or "") in ("stored", "unsure", "invalid")


def set_gate(repo, cand_id: int | None, gate: str, decision: dict | None = None) -> None:
    """Дописать, что сделали ворота. Рядом с ответом, а не вместо него.

    `decision` — основание: причина политики, её версия, checkpoint применения. Без
    него «применено» нельзя было связать с правилами, по которым это решили.
    """
    if repo is None or not cand_id:
        return
    try:
        if decision is not None:
            ensure(repo.db)
            repo.db.execute("UPDATE candidates SET gate=?, decision_json=? WHERE id=?",
                            (gate, json.dumps(decision, ensure_ascii=False), cand_id))
        else:
            repo.db.execute("UPDATE candidates SET gate=? WHERE id=?", (gate, cand_id))
        repo.db.commit()
    except Exception as exc:                                       # noqa: BLE001
        log.debug("candidates: gate not recorded for %s: %s", cand_id, exc)
