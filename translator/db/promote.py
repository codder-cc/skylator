"""Что из слоя кандидатов можно перенести в корпус — и почему остальное держим.

Слой хранит каждый ответ машин с вердиктом судьи. Судья проверен на ста реальных
парах и порчу почти не пропускает, но у него есть три слепых пятна, найденных чтением
того, что он одобрил:

    РОД ГОВОРЯЩЕГО   судья видит только английский и два русских текста. «Когда я была
                     маленькой» и «когда я был маленьким» для него равноценны, и он
                     выбирает по другим признакам — а пол персонажа меняется.
    ЛОМАНАЯ РЕЧЬ     «Berg try save froma, friend. Him use metal club…» — модель чинит
                     грамматику и по дороге теряет смысл («Тот ударил его в спину»),
                     а судья предпочитает гладкий русский.
    ИМЕНА            «the Reach» стал «Ривейн», хотя игра пишет «Предел». Такие имена
                     лежат в отложенной части официальной таблицы, и ни подсказка
                     модели, ни правила о них не знали.

Поэтому решение здесь — не «судья сказал да», а судья И ни одного из этих признаков.
Каждая удержанная строка получает причину, чтобы политику можно было проверить на
данных до того, как тронуть корпус.
"""
from __future__ import annotations

import collections
import re
from functools import lru_cache

_CYR = re.compile(r"[А-Яа-яЁё]+(?:-[А-Яа-яЁё]+)?")
_STOP = re.compile(r"[.!?,;:…—–\"«»()]")


# ── род первого лица ────────────────────────────────────────────────────────

def _word_gender(word: str) -> str | None:
    """'m'|'f' для глагола прошедшего времени или краткого прилагательного."""
    from translator.characters.gender import _morph
    parses = _morph().parse(word.lower())
    if not parses:
        return None
    p = parses[0]
    if p.tag.number != "sing":
        return None
    if ("VERB" in p.tag and p.tag.tense == "past") or "ADJS" in p.tag or "PRTS" in p.tag:
        return {"masc": "m", "femn": "f"}.get(p.tag.gender)
    return None


def first_person_gender(text: str) -> set:
    """Роды, в которых говорит «я»: «я была», «я рад», «я уверена».

    Смотрим только несколько слов после «я» и не переходим через знак препинания —
    иначе «я думала, что он ушёл» дало бы и женский, и мужской.
    """
    out: set = set()
    for chunk in _STOP.split(text or ""):
        words = _CYR.findall(chunk)
        for i, w in enumerate(words):
            if w.lower() != "я":
                continue
            for nxt in words[i + 1:i + 4]:
                g = _word_gender(nxt)
                if g:
                    out.add(g)
                    break
    return out


# ── ломаная речь ────────────────────────────────────────────────────────────

# Признаки берутся только такие, каких в обычной речи не бывает. Первая версия ловила
# «длинную реплику без вспомогательных глаголов» и «me want» — и держала 297 строк,
# среди которых не нашлось ни одной ломаной: «aren't», «I'm», «don't» регулярка
# вспомогательными не считала, а «it makes me want to cry» — обычный английский.
_PIDGIN_MARKS = re.compile(
    # местоимение-дополнение в роли подлежащего, в начале фразы: «Him use metal club».
    # Her сюда не входит: «Her eyes were blue» — притяжательное, а не ломаная речь.
    r"(?:^|[.!?]\s+)(?:Him|Me|Us|Them) (?!and\b|or\b|too\b)[a-z]+\b"
    # глагол с 'd вместо прошедшего времени: «he be'd ill», «become'd wife»
    r"|\b(?:be|become|come|go|know|see|make|take|give|get|do|say|run|fight)'d\b"
    # «no move», «no want» — отрицание без вспомогательного
    r"|(?<!, )(?<!\bhave )\bno (?:move|want|like|know|go|understand|fight|die|see)\b")


def looks_pidgin(original: str) -> bool:
    """Ломаная речь в самой строке: «Him use metal club», «be'd ill», «no move»."""
    return bool(_PIDGIN_MARKS.search(original or ""))


# ── имена ───────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _names() -> dict:
    """Однословные имена → русское написание, из ПОЛНОЙ официальной таблицы.

    Полной — вместе с отложенной частью: «The Reach → Предел» и «Brynjolf →
    Бриньольф» лежат именно там. Эталон нужен для замеров; здесь проверяется, не
    потерял ли новый перевод имя, которое было в хранимом.
    """
    # Файл читается напрямую: load_official закэширован, и в процессе, где его уже
    # позвали без отложенной части (в мастере — всегда), переключить её нельзя.
    import json
    from translator.validation.authority import _TABLE_PATH
    try:
        table = dict(json.loads(_TABLE_PATH.read_text(encoding="utf-8")))
    except Exception:                                              # noqa: BLE001
        table = {}
    try:
        from translator.validation.terminology import canonical, load_terms
        for k, v in load_terms().items():
            table.setdefault(k, canonical(v))
    except Exception:                                              # noqa: BLE001
        pass
    from translator.validation.official_context import single_names_from
    return single_names_from(table)


def _stem(ru: str) -> str:
    low = ru.lower().replace("ё", "е")
    return low[:-2] if len(low) > 6 else low[:-1] if len(low) > 4 else low


def lost_names(original: str, stored: str, fresh: str) -> list:
    """Имена, которые хранимый перевод писал по-игровому, а новый — потерял."""
    names = _names()
    s_low = (stored or "").lower().replace("ё", "е")
    f_low = (fresh or "").lower().replace("ё", "е")
    lost = []
    for w in set(re.findall(r"\b[A-Z][a-z'\-]{3,}\b", original or "")):
        ru = names.get(w)
        if not ru:
            continue
        st = _stem(ru)
        if st in s_low and st not in f_low:
            lost.append((w, ru))
    return lost


# ── решение ─────────────────────────────────────────────────────────────────

def decide(row, speaker_gender: str | None = None, speaker_pidgin: bool = False):
    """(True, 'promote') или (False, причина). row — строка таблицы candidates."""
    if (row["judge"] or "") != "fresh":
        return False, f"judge:{row['judge'] or 'none'}"
    if (row["rules_status"] or "") != "translated":
        return False, "rules"
    stored = row["rival"] or row["stored_at_arrival"] or ""
    fresh = row["translation"] or ""
    original = row["original"] or ""

    gf, gs = first_person_gender(fresh), first_person_gender(stored)
    if speaker_gender:
        if gf and gf != {speaker_gender}:
            return False, "gender:contradicts_speaker"
    elif gf and gs and gf != gs:
        return False, "gender:changed_speaker_unknown"

    if speaker_pidgin or looks_pidgin(original):
        return False, "pidgin"

    if lost_names(original, stored, fresh):
        return False, "names:lost"
    return True, "promote"


# ── план по всему слою ──────────────────────────────────────────────────────

def _speakers():
    """Пол и манера речи говорящих. Без них решаем по одной строке."""
    try:
        from translator.characters import speakers
        from translator.config import load_config
        mods = load_config().paths.mods_dir
        game = (mods.parents[1] / "STOCK GAME" / "Data") if mods else None
        return speakers, speakers.load(mods, game if game and game.is_dir() else None)
    except Exception:                                              # noqa: BLE001
        return None, {}


def plan(con, since: float = 0.0) -> list:
    """[(row, ok, причина)] по каждому рассуженному ответу слоя.

    Если на строку пришло несколько ответов, в корпус может уйти только самый
    поздний из одобренных: дальше он всё равно заменил бы предыдущий.
    """
    from translator.data_manager.string_manager import _identity_from_key
    sp, state = _speakers()
    rows = con.execute("SELECT * FROM candidates WHERE produced_at > ? AND judge IS NOT NULL "
                       "AND judge <> 'broken_judge' ORDER BY produced_at", (since,)).fetchall()
    out = []
    for r in rows:
        ident = _identity_from_key(r["key"] or "")
        form_id = ident[0] if ident else None
        g = sp.gender_for(r["esp_name"], form_id) if sp else None
        card = sp.card_for(r["esp_name"], form_id or "", state) if (sp and state) else None
        if g is None and card is not None:
            g = {"male": "m", "female": "f", "m": "m", "f": "f"}.get(
                (getattr(card, "sex", "") or "").lower())
        ok, why = decide(r, speaker_gender=g,
                         speaker_pidgin=bool(card and getattr(card, "pidgin", False)))
        out.append((r, ok, why))
    latest: dict = {}
    for i, (r, ok, _why) in enumerate(out):
        if ok:
            prev = latest.get(r["string_id"])
            if prev is not None:
                pr = out[prev][0]
                out[prev] = (pr, False, "superseded")
            latest[r["string_id"]] = i
    return out
