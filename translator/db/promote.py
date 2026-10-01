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


def second_person_gender(text: str) -> set:
    """Роды, в которых говорят с «ты»: «ты нашёл», «ты уверена»."""
    out: set = set()
    for chunk in _STOP.split(text or ""):
        words = _CYR.findall(chunk)
        for i, w in enumerate(words):
            if w.lower() != "ты":
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
    # местоимение-дополнение в роли подлежащего, в начале фразы, и за ним голый
    # глагол: «Him use metal club», «Me has no idea». «Us being accomplices» и
    # «Me of course!» — разговорная речь, не ломаная: -ing и служебные слова не в счёт.
    r"(?:^|[.!?]\s+)(?:Him|Me|Them) "
    r"(?!and\b|or\b|too\b|of\b|neither\b|either\b|both\b|first\b|again\b|alone\b)"
    r"[a-z]+(?<!ing)\b"
    # глагол с 'd вместо прошедшего времени: «he be'd ill», «become'd wife»
    r"|\b(?:be|become|come|go|know|see|make|take|give|get|do|say|run|fight|lose|forget)'d\b"
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
    # Только имя внутри фразы: там заглавная значит имя. Короткую подпись («Moth»,
    # «Reanimate», «Diamond») целиком судит правило официальной таблицы, а в таблице
    # имён такие слова — шум: «Moth → Мот» это имя персонажа, и правка по нему
    # превратила «Моль» в «Мот».
    if len((original or "").split()) < 4:
        return lost
    from translator.validation.official_context import _mid_sentence_caps
    for w in set(_mid_sentence_caps(original or "")):
        # «Whiterun's» — то же имя: без этого «Вайтрана» → «Уитеруна» проходило.
        w = w[:-2] if w.endswith(("'s", "’s")) else w
        if len(w) < 4:
            continue
        ru = names.get(w)
        if not ru:
            continue
        st = _stem(ru)
        # Беглая гласная: «свиток» — «свитка», «Солитьюд» не склоняется так, а «конец» —
        # «конца». Без этого правильная словоформа считалась потерей имени, а починка
        # делала из «Древнего свитка» «свитока».
        low = ru.lower().replace("ё", "е")
        alt = low[:-2] + low[-1] if len(low) > 4 and low[-2] in "ое" else None
        if st in s_low and st not in f_low and not (alt and alt in f_low):
            lost.append((w, ru))
    return lost


# Подписи игры: названия (FULL), кнопки (ITXT), цели заданий (NNAM), реплики-кнопки
# игрока (DIAL/FULL, RNAM) и описания эффектов (MGEF/DNAM). Здесь решают конвенции
# игры, а не живость русского, и судья о них не знает. Замер на эталоне после первого
# применения: из 13 подписей, где хранимое совпадало с официальным, судья заменил 11 —
# «Вызов гаргульи» → «Призвать гаргулью», «Уйти» → «Уходи.», «Навык … увеличивается
# на <mag> ед.» → «Увеличивает навык … на <mag> единиц», «Я хочу купить дом» → «Я хотел
# бы купить дом» (род игрока, которого игра намеренно избегает).
_LABEL_FIELDS = {"FULL", "ITXT", "NNAM", "RNAM"}


def is_label(row) -> bool:
    rt, ft = (row["rec_type"] or ""), (row["field_type"] or "")
    return (ft in _LABEL_FIELDS or (rt, ft) == ("MGEF", "DNAM")
            or len((row["original"] or "").strip()) <= 25)


def _changed_share(a: str, b: str) -> float:
    """Доля слов, которые правка тронула — и заменённых, и дописанных.

    Раньше делитель был длиной старого текста, и дописанная фраза не считалась вовсе:
    «one two three» → «one two three four five six» давало 0%. Правщик, приписавший
    к строке что угодно, выглядел точечным.
    """
    import difflib
    wa, wb = a.split(), b.split()
    same = sum(bl.size for bl in difflib.SequenceMatcher(a=wa, b=wb).get_matching_blocks())
    return 1 - same / max(len(wa), len(wb), 1)


# ── решение ─────────────────────────────────────────────────────────────────

_TY = re.compile(r"(?<![А-Яа-яЁё])(ты|тебя|тебе|тобой|тобою|твой|твоя|твоё|твое|твои|"
                 r"твоего|твоей|твоему|твоим|твоих|твоими|твою)(?![А-Яа-яЁё])", re.I)
_VY = re.compile(r"(?<![А-Яа-яЁё])(вы|вас|вам|вами|ваш|ваша|ваше|ваши|вашего|вашей|"
                 r"вашему|вашим|ваших|вашими|вашу)(?![А-Яа-яЁё])", re.I)


def address_form(text: str) -> str | None:
    """«ty», «vy» или None — как текст обращается к собеседнику, если однозначно.

    Промпт велит «ты» (так говорит официальная игра в 86% реплик, где форма видна), а
    судья этого правила не знает: на контрольном пакете он принял «Ты успешно помог» →
    «Вы успешно помогли». Смена «ты» на «вы» — не улучшение, а отказ от конвенции.
    """
    t, v = bool(_TY.search(text or "")), bool(_VY.search(text or ""))
    return "ty" if t and not v else "vy" if v and not t else None


def policy_rev() -> str:
    """Отпечаток правил применения: файлы, от которых зависит решение."""
    import hashlib
    from pathlib import Path as _P
    root = _P(__file__).resolve().parents[2]
    h = hashlib.sha1()
    for rel in ("translator/db/promote.py", "translator/validation/official_context.py",
                "translator/validation/authority.py", "translator/validation/quality.py"):
        try:
            h.update((root / rel).read_bytes())
        except OSError:
            h.update(rel.encode())
    return h.hexdigest()[:12]


def decide(row, speaker_gender: str | None = None, speaker_pidgin: bool = False):
    """(True, 'promote') или (False, причина). row — строка таблицы candidates.

    judge='termfix' — точечная правка имени по заданию прохода согласованности.
    Судьи у неё нет, поэтому вместо его вердикта проверяется, что правка точечная:
    правщик, переписавший строку целиком, хуже никакого.
    """
    if (row["judge"] or "") == "termfix":
        if _changed_share(row["rival"] or row["stored_at_arrival"] or "",
                          row["translation"] or "") > 0.34:
            return False, "termfix:too_wide"
    elif (row["judge"] or "") != "fresh":
        return False, f"judge:{row['judge'] or 'none'}"
    if (row["rules_status"] or "") != "translated":
        return False, "rules"
    # Подпись меняется только по явной причине: старый текст сломан по правилам
    # (решается в plan, до этой проверки) или имя чинится по заданию. Вкус судьи
    # против конвенции игры — не причина.
    stored = row["rival"] or row["stored_at_arrival"] or ""
    fresh = row["translation"] or ""
    original = row["original"] or ""
    evidenced = False
    if (row["judge"] or "") == "fresh" and is_label(row) \
            and not ("_stored_broken" in row.keys() and row["_stored_broken"]):
        # Вкус судьи подпись не меняет, а сама игра — может: если её записи пишут эту
        # подпись так же, как новый ответ, и ни одна — как хранимый текст.
        from translator.validation.official_context import label_evidence, LABEL_EVIDENCE_MIN
        if not (label_evidence(original, fresh) >= LABEL_EVIDENCE_MIN
                and label_evidence(original, stored) == 0):
            return False, "label:convention"
        evidenced = True

    gf, gs = first_person_gender(fresh), first_person_gender(stored)
    if speaker_gender:
        if gf and gf != {speaker_gender}:
            return False, "gender:contradicts_speaker"
    elif gf and gs and gf != gs:
        return False, "gender:changed_speaker_unknown"

    if speaker_pidgin or looks_pidgin(original):
        return False, "pidgin"

    if address_form(stored) == "ty" and address_form(fresh) == "vy":
        return False, "address:ty_to_vy"

    # Пол игрока неизвестен, и игра говорит с ним в мужском роде. Свежая выборка
    # 28.09: из 16 ухудшений среди одобренных три — «ты нашёл» → «ты нашла».
    if second_person_gender(stored) == {"m"} and "f" in second_person_gender(fresh):
        return False, "gender:player_changed"

    if lost_names(original, stored, fresh):
        return False, "names:lost"
    return True, ("promote:label_evidence" if evidenced else "promote")


def repair_gender(row, speaker_gender: str | None) -> str | None:
    """Привести «я» к полу говорящего без ИИ — и проверить, что вышло.

    Правка (`gender.enforce`) знает глаголы прошедшего времени, но не все краткие
    прилагательные: «я бы дал больше» она исправит, а «я слишком щедра» оставит, и
    строка станет смешанной. Поэтому результат принимается, только если после правки
    род «я» ровно тот, что у говорящего.
    """
    if not speaker_gender:
        return None
    from translator.characters.gender import enforce
    fixed = enforce(row["original"] or "", row["translation"] or "", speaker_gender)
    if fixed == (row["translation"] or ""):
        return None
    if first_person_gender(fixed) != {speaker_gender}:
        return None
    return fixed


# Дефекты хранимого текста, при которых он проигрывает чистому новому без судьи.
# «Потеряно отрицание» сюда не входит: правило шумит на риторических вопросах —
# «Isn't that a pity» → «Ну что ж, как жаль» оно считает потерей, и замена на
# «неужели это не жаль» делала строку хуже.
_HARD_STORED = ("glossary", "angle brackets", "untranslated", "mixed alphabets",
                "foreign script", "markup lost", "model commentary", "missing ",
                "prompt echoed")


def stored_is_broken(row, terms=None) -> bool:
    """Правила бракуют хранимый текст по признаку, который не бывает вкусовщиной."""
    from translator.validation.quality import compute_string_status
    stored = row["rival"] or row["stored_at_arrival"] or ""
    if not stored:
        return False
    try:
        _s, _t, issues, status = compute_string_status(
            row["original"] or "", stored, terms, row["rec_type"], row["field_type"])
    except Exception:                                              # noqa: BLE001
        return False
    if status != "needs_review":
        return False
    for i in issues or []:
        msg = (i.get("message") if isinstance(i, dict) else str(i)) or ""
        if msg.startswith(_HARD_STORED):
            return True
    return False


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


def plan(con, since: float = 0.0, holdout_guard: bool = True) -> list:
    """[(row, ok, причина, текст)] по каждому рассуженному ответу слоя.

    Текст — то, что пойдёт в корпус: обычно ответ модели как есть, а для строки, где
    род говорящего чинится без ИИ, — исправленный.

    Если на строку пришло несколько ответов, в корпус может уйти только самый
    поздний из одобренных: дальше он всё равно заменил бы предыдущий.
    """
    from translator.data_manager.string_manager import _identity_from_key
    sp, state = _speakers()
    try:
        from translator.validation.terminology import load_terms
        terms = load_terms()
    except Exception:                                              # noqa: BLE001
        terms = None
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
        pidgin = bool(card and getattr(card, "pidgin", False))
        from translator.validation.authority import is_holdout_line
        # holdout_guard=False — только для замера политики на самой выборке.
        if holdout_guard and is_holdout_line(r["original"] or ""):
            out.append((r, False, "holdout", r["translation"]))
            continue
        ok, why = decide(r, speaker_gender=g, speaker_pidgin=pidgin)
        text = r["translation"]
        if why == "gender:contradicts_speaker":
            fixed = repair_gender(r, g)
            if fixed:
                patched = dict(r)
                patched["translation"] = fixed
                ok2, why2 = decide(patched, speaker_gender=g, speaker_pidgin=pidgin)
                if ok2:
                    ok, why, text = True, "promote:gender_repaired", fixed
        if why == "names:lost":
            fixed = repair_names(r["original"] or "", r["rival"] or r["stored_at_arrival"] or "",
                                 r["translation"] or "")
            if fixed:
                patched = dict(r)
                patched["translation"] = fixed
                ok2, why2 = decide(patched, speaker_gender=g, speaker_pidgin=pidgin)
                if ok2:
                    ok, why, text = True, "promote:names_repaired", fixed
        # Судья не уверен, но хранимый текст сломан по правилам, а новый чист: «строго
        # лучше» здесь решают правила, а не вкус. Если судья прямо выбрал хранимый —
        # не спорим. Все фильтры (род, ломаная речь, имена) действуют и здесь.
        if (why in ("judge:unsure", "judge:invalid", "label:convention")
                and r["rules_status"] == "translated" and stored_is_broken(r, terms)):
            patched = dict(r)
            patched["judge"] = "fresh"
            patched["_stored_broken"] = True
            ok2, why2 = decide(patched, speaker_gender=g, speaker_pidgin=pidgin)
            if ok2:
                ok, why, text = True, "promote:stored_broken", r["translation"]
            else:
                why = why2
        out.append((r, ok, why, text))
    latest: dict = {}
    for i, (r, ok, _why, _t) in enumerate(out):
        if ok:
            prev = latest.get(r["string_id"])
            if prev is not None:
                pr, _o, _w, pt = out[prev]
                out[prev] = (pr, False, "superseded", pt)
            latest[r["string_id"]] = i
    return out


# ── починка имени ───────────────────────────────────────────────────────────
#
# Новый ответ бывает лучше старого во всём, кроме имени: «Тальмор» вместо «Талмор»,
# «Мейвен» вместо «Мавен». Удерживать такую правку целиком — терять улучшение (на
# выборке 3 из 6 удержанных фильтром имён). Здесь имя чинится, а решение принимается
# заново: если после починки правка проходит все фильтры, в корпус идёт починенный
# текст. Чинится только имя на согласную: основа «Талмор» в «Талмором», «Талморский»
# видна целиком, а у «Мирабелла» склонение меняет саму основу, и такие остаются
# удержанными.
_NAME_ENDINGS = ("", "а", "у", "ом", "е", "ы", "ов", "ам", "ами", "ах", "ий", "ия", "ию",
                 "ский", "ского", "скому", "ским", "ском", "ская", "ской", "скую", "ские",
                 "ских", "скими", "цы", "цев", "цам", "цами", "цах", "ец", "ца", "цу",
                 "цем", "це", "ец", "овский", "овского")
_RU_WORD = re.compile(r"[А-Яа-яЁё]+")
_NAME_MIN_SOUND = 0.7


def _sound_ratio(a: str, b: str) -> float:
    import difflib
    from translator.characters.cards import _sound
    x, y = _sound(a), _sound(b)
    return difflib.SequenceMatcher(a=x, b=y).ratio()


def repair_names(original: str, stored: str, fresh: str) -> str | None:
    """Новый текст с исправленными официальными именами, или None, если нечего или
    нельзя чинить. Чинится каждое потерянное имя; если хоть одно не чинится — None."""
    lost = lost_names(original, stored, fresh)
    if not lost:
        return None
    text = fresh or ""
    for _en, ru in lost:
        ru = ru.strip()
        if not ru or ru[-1].lower() in "аяоеёиыуюэь" or " " in ru:
            return None
        best = None
        for m in _RU_WORD.finditer(text):
            tok = m.group(0)
            if tok[:1].lower() != ru[:1].lower():
                continue
            for end in _NAME_ENDINGS:
                if end and not tok.lower().endswith(end):
                    continue
                stem = tok[:len(tok) - len(end)] if end else tok
                if len(stem) < 3:
                    continue
                r = _sound_ratio(stem, ru)
                if r >= _NAME_MIN_SOUND and (best is None or r > best[0]):
                    best = (r, m.start(), m.end(), end, tok[:1].isupper())
        if best is None:
            return None
        _r, a, b, end, upper = best
        new = (ru if upper else ru[:1].lower() + ru[1:]) + end
        text = text[:a] + new + text[b:]
    return text if not lost_names(original, stored, text) else None
