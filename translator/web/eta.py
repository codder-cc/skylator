"""Сколько осталось — в работе, а не в штуках.

Прежний ETA делил оставшиеся СТРОКИ на наблюдаемые строки в секунду. При однородной
очереди это верно, а у нас очередь однородной не бывает:

    M5   142 строки/час   но 118 токенов/с      средняя оставшаяся строка 1 426 знаков
    M1   352 строки/час   но 7,8 токенов/с      средняя оставшаяся строка   172 знака

M5 в пятнадцать раз быстрее по токенам и в два с половиной раза медленнее по строкам —
просто потому, что ему достались книги. Считать в штуках при такой разнице значит
считать неизвестно что.

Хуже: `sweep` выдаёт строки по убыванию длины. Наблюдаемая скорость в штуках растёт по
ходу пакета сама собой, поэтому ETA в штуках систематически завышен в начале и занижен
в конце — он ошибается предсказуемо, а это худший вид ошибки, потому что похож на правду.

ЕДИНИЦА

Токен, и стоимость строки считается по замеренному, а не по паспортному:

    служебная часть промпта   832 токена НА ЗАПРОС  (замер на реальном build_prompt)
    английский вход           3,99 знака на токен   (токенизатор Qwen, 20 000 строк)
    русский выход             2,64 знака на токен, и русского выходит в 1,006 раза
                              больше английского по знакам (32 716 438 против 32 537 055)

Служебное делится на размер батча — это единственное место, где батч вообще виден в
оценке, и он там виден правильно: при батче 4 на строку в 17 токенов приходится 208
служебных, при 32 — 26.

Побочное следствие, о котором стоит помнить: служебное СЖИМАЕТ разницу между длинной
строкой и короткой. Книга в 1 426 знаков стоит 1 108 токенов, реплика в 172 знака — 316,
то есть втрое, а не вдевятеро, как следует из одних знаков. Это тоже причина не считать
в штуках, но и не считать в голых знаках.

СКОРОСТЬ

Не берётся из телеметрии, и модель с ней сознательно не сверяется. `tps` агента у M5
выходит 118 против 44 по этой модели: он меряет генерацию во время работы, без префилла
и без пауз между батчами, и на разных бэкендах считается по-разному. Сверять модель с
ним было бы подгонкой под число, которое означает другое.

Поэтому единица здесь — ОТНОСИТЕЛЬНАЯ мера работы, а абсолютная скорость меряется в ней
же: сколько этой работы ушло за наблюдаемое время. Постоянный множитель между моделью и
реальностью сокращается сам, а в измеренную скорость входят и простои, и занятость
машины хозяином, и падение частот, и смена длины строк — всё, чего в паспортных цифрах
нет и быть не может.

ПОЧЕМУ СЧИТАЕТСЯ ПО ЗАПРОСУ, А НЕ НА HEARTBEAT

Оценка нужна тому, кто спросил, а heartbeat приходит каждые несколько секунд от каждой
машины. Замеры складываются в окно здесь же, при обращении, и не трогают горячий путь
приёма результатов.
"""
from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger(__name__)

# Замерено на этой установке. Менять — только вместе с новым замером.
PROMPT_OVERHEAD_TOKENS = 832.0
EN_CHARS_PER_TOKEN     = 3.99
RU_CHARS_PER_TOKEN     = 2.64
RU_TO_EN_CHARS         = 1.006
DEFAULT_BATCH          = 4

# Реже, чем раз в 15 секунд, пересчитывать нечего: за это время не доставляется и одной
# книги, а запрос по таблице на два миллиона строк не бесплатен.
_MIN_SAMPLE_SEC = 15.0
_WINDOW         = 12          # замеров в окне; при 15 с это три минуты наблюдения

_LOCK = threading.Lock()
_SAMPLES: dict = {}           # assignment_id → [(время, сделанная работа), ...]
_TOTALS: dict = {}            # assignment_id → полная работа пакета


def string_work(en_chars: int, batch_size: int = DEFAULT_BATCH) -> float:
    """Во что обходится одна строка, в токенах."""
    n = max(int(batch_size or DEFAULT_BATCH), 1)
    return (PROMPT_OVERHEAD_TOKENS / n
            + en_chars / EN_CHARS_PER_TOKEN
            + en_chars * RU_TO_EN_CHARS / RU_CHARS_PER_TOKEN)


def _work_of(db, assignment_id: str, undelivered_only: bool, batch: int) -> tuple:
    """(строк, работы в токенах) по назначению."""
    where = "a.assignment_id=?" + (" AND a.delivered=0" if undelivered_only else "")
    row = db.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(LENGTH(s.original)), 0) chars "
        "FROM assignment_strings a JOIN strings s ON s.id = a.string_id "
        f"WHERE {where}", (assignment_id,)).fetchone()
    n = int(row["n"] if hasattr(row, "keys") else row[0])
    chars = int(row["chars"] if hasattr(row, "keys") else row[1])
    if not n:
        return 0, 0.0
    # Служебное платится за запрос, поэтому на пакет оно умножается на число строк,
    # делённое на батч, — то же самое, что стоимость на строку, сложенная по строкам.
    return n, (PROMPT_OVERHEAD_TOKENS * n / max(batch, 1)
               + chars / EN_CHARS_PER_TOKEN
               + chars * RU_TO_EN_CHARS / RU_CHARS_PER_TOKEN)


def _batch_of(job) -> int:
    try:
        return int((job.params or {}).get("batch_size") or DEFAULT_BATCH)
    except Exception:
        return DEFAULT_BATCH


def eta_for_job(job, db, registry) -> float | None:
    """Секунды до конца пакета, или None — когда сказать нечем.

    Молчание здесь честнее выдумки: одного замера мало, чтобы знать скорость, а пакет
    без незакрытых строк уже кончился.
    """
    if db is None or registry is None:
        return None
    ids = list((job.params or {}).get("offline_job_ids") or [])
    if not ids:
        return None
    batch = _batch_of(job)
    now = time.time()
    per_machine: list[float] = []

    for aid in ids:
        try:
            _n_left, left = _work_of(db, aid, True, batch)
        except Exception as exc:                                   # noqa: BLE001
            log.debug("eta: assignment %s unreadable: %s", str(aid)[:8], exc)
            continue
        if left <= 0:
            continue

        with _LOCK:
            if aid not in _TOTALS:
                try:
                    _n_all, whole = _work_of(db, aid, False, batch)
                    _TOTALS[aid] = whole
                except Exception:
                    _TOTALS[aid] = 0.0
            done = max(_TOTALS.get(aid, 0.0) - left, 0.0)
            hist = _SAMPLES.setdefault(aid, [])
            if not hist or now - hist[-1][0] >= _MIN_SAMPLE_SEC:
                hist.append((now, done))
                del hist[:-_WINDOW]
            rate = 0.0
            if len(hist) >= 2:
                dt = hist[-1][0] - hist[0][0]
                dw = hist[-1][1] - hist[0][1]
                if dt > 0 and dw > 0:
                    rate = dw / dt
        if rate > 0:
            per_machine.append(left / rate)

    # Машины работают параллельно, поэтому ждать придётся ту, что закончит последней.
    # Складывать остатки и делить на сумму скоростей значило бы обещать, что
    # освободившаяся машина заберёт чужую работу, — а она этого не делает: пакет
    # выдан ей одной.
    return max(per_machine) if per_machine else None


def forget(assignment_id: str) -> None:
    """Забыть наблюдения по закрытому пакету."""
    with _LOCK:
        _SAMPLES.pop(assignment_id, None)
        _TOTALS.pop(assignment_id, None)


def install(app) -> None:
    """Подключить оценку к job_manager, не заводя в нём зависимости от базы."""
    from translator.web.job_manager import JobManager

    def provider(job):
        try:
            repo = app.config.get("STRING_REPO")
            return eta_for_job(job, repo.db if repo else None,
                               app.config.get("WORKER_REGISTRY"))
        except Exception as exc:                                   # noqa: BLE001
            log.debug("eta provider failed: %s", exc)
            return None

    JobManager.set_eta_provider(provider)
