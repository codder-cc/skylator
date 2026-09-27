"""Одно имя внутри реплик мода — одно написание.

`consistency.py` сводит имена, которые стоят строкой целиком (FULL у NPC, локаций,
предметов). Имя ВНУТРИ реплики он не видит, а разнобой живёт именно там: у Берга
`Jo'tun` записан как Йо'тун, Йотун, Джо'тун, Джотун и ютун, построчный перевод даёт
«Лиз» в одной строке и «Лизз» в соседней. Каждая строка по отдельности безупречна;
ошибка — в отношении между ними, и увидеть её можно только по моду целиком.

КАК УЗНАТЬ ИМЯ В ПЕРЕВОДЕ

Выравнивателя слов у нас нет. Но имя собственное из выдуманного языка не переводят, а
транслитерируют, поэтому его узнают по звучанию (`cards._sound`): среди русских слов
строки берётся то, чей звуковой ключ ближе всего к ключу английского имени. Падеж
снимается отбрасыванием окончания — «Йотуна» и «Йотуну» одно написание.

ЧТО СЧИТАЕТСЯ РАЗНОБОЕМ

Имя встречается в моде хотя бы в трёх строках, узнано хотя бы в двух разных
написаниях, и у одного из них явное большинство. Если имя есть в официальной таблице,
правильным считается её написание, а не большинство: большинство не доказывает
правильность, оно только показывает, что коллекция не определилась.

Модуль ничего сам не исправляет. Он выдаёт задания для точечной правки — строку и
требуемое написание, — а правку и перепроверку делает тот же путь, что для потерянных
имён (scripts/repair_held.py).
"""
from __future__ import annotations

import collections
import difflib
import re

_CYR_TOKEN = re.compile(r"[А-ЯЁ][а-яё]+(?:['’\-][А-ЯЁа-яё][а-яё]+)*")
MIN_LINES = 3
MAJORITY = 0.6
MAX_DIST = 0.45


def _key(token: str) -> str:
    """Написание для сравнения: ё=е, у составного — первая часть («Анвил-Харборе»)."""
    low = token.lower().replace("’", "'").replace("ё", "е")
    return low.split("-", 1)[0] if "-" in low else low


def _prefix(want: str) -> str:
    """Эталон без падежного хвоста: всё, что начинается с этого, — то же написание.

    Лемма тут не годится: pymorphy склоняет незнакомые имена наугад, и «Мары» с
    «Марой» у него разные слова, а «Скайрим» теряет «-им» как прилагательное.
    Срезать с эталона одну-две последние буквы надёжнее: «Мара» → «мар» принимает
    «Мары», «Марой»; «Солитьюд» → «солить» не принимает «Солитюд»;
    «Драконорожденный» → «драконорожденн» принимает все падежи.
    """
    k = _key(want)
    return k[:-2] if len(k) > 6 else k[:-1] if len(k) > 3 else k


def same(token: str, want: str) -> bool:
    return _key(token).startswith(_prefix(want))


def repair_tasks(clusters: list[dict]) -> dict:
    """{string_id: «English = Русское»} — только там, где эталон дала игра.

    Разнобой без официального написания в задания не идёт: узнавание по звучанию на
    одиночных строках путает имена («Julius» узнан как «Юлия», «Dwarves» — как
    «Давайте»), и «большинство» там не доказывает ничего.
    """
    tasks: dict = collections.defaultdict(list)
    for c in clusters:
        if not c["official"]:
            continue
        for rid, _tok in c["off"]:
            req = f"{c['name']} = {c['want']}"
            if req not in tasks[rid]:
                tasks[rid].append(req)
    return {rid: "; ".join(reqs) for rid, reqs in tasks.items()}


def _dist(a: str, b: str) -> float:
    return 1.0 - difflib.SequenceMatcher(a=a, b=b).ratio()


def rendering_of(en_name: str, translation: str) -> str | None:
    """Как это имя записано в переводе — токен, ближайший по звучанию, или None."""
    from translator.characters.cards import _sound
    key = _sound(en_name)
    if len(key) < 3:
        return None
    best, best_d = None, 1.0
    for tok in _CYR_TOKEN.findall(translation or ""):
        # «Nord» узнавался как «Но» и «Иногда»: короткое слово близко к чему угодно
        if len(tok) < max(4, len(key) - 2):
            continue
        d = _dist(_sound(_key(tok)), key)
        if d < best_d:
            best, best_d = tok, d
    return best if best is not None and best_d <= MAX_DIST else None


def scan(rows, official: dict | None = None) -> list[dict]:
    """rows: [(id, mod, original, translation)]. Возвращает кластеры с разнобоем.

    Каждый кластер: имя, мод, написания с числом строк, требуемое написание и строки,
    которые от него отклоняются.
    """
    from translator.validation.official_context import _COMMON_EN, _mid_sentence_caps
    official = official or {}
    seen: dict = collections.defaultdict(list)       # (mod, en) → [(id, token)]
    for rid, mod, en, ru in rows:
        if not ru:
            continue
        for w in set(_mid_sentence_caps(en or "")):
            w = w[:-2] if w.endswith("'s") else w
            if len(w) < 4 or w.lower() in _COMMON_EN:
                continue
            tok = rendering_of(w, ru)
            if tok:
                seen[(mod, w)].append((rid, tok))
    out = []
    for (mod, en), hits in seen.items():
        if len({rid for rid, _ in hits}) < MIN_LINES:
            continue
        tokens = collections.Counter(t for _, t in hits)
        want_ru = official.get(en)
        if want_ru:
            if not any(same(t, want_ru) for t in tokens):
                # Официального написания нет ни в одной строке мода — слово здесь в
                # другом смысле: «Moth» в таблице — персонаж «Мот», а тут мотылёк.
                continue
        else:
            # эталон — написание, которое покрывает больше всего строк; из равных —
            # самое короткое (обычно именительный падеж)
            def cover(t):
                return sum(n for u, n in tokens.items() if same(u, t))
            want_ru = max(sorted(tokens, key=len), key=cover)
            if cover(want_ru) / sum(tokens.values()) < MAJORITY:
                continue
        off = [(rid, t) for rid, t in hits if not same(t, want_ru)]
        if not off:
            continue
        out.append({"mod": mod, "name": en, "want": want_ru,
                    "official": bool(official.get(en)),
                    "variants": dict(tokens.most_common()),
                    "off": off})
    out.sort(key=lambda c: -len(c["off"]))
    return out
