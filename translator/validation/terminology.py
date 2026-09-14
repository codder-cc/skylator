"""
C — terminology consistency checking.

A curated glossary (data/skyrim_terms.json, EN→RU) is injected into prompts, but nothing
verified the model actually applied it. Across ~3,800 mods the same term (a place, character,
or item name) can drift into several different translations. This finds that drift: translated
strings whose ORIGINAL contains a glossary term but whose TRANSLATION is missing the expected
term translation — the inconsistencies to review/fix.

Pure functions over a list of string rows so they're trivially testable; the route feeds them
the DB rows for a mod (or the whole store).
"""
from __future__ import annotations

import re
from functools import lru_cache


def _contains_word(haystack: str, needle: str) -> bool:
    """Case-insensitive whole-word-ish containment (word boundaries, so 'Iron' doesn't match
    'Ironed'). Falls back to substring for multi-word / non-word terms."""
    h = (haystack or "").lower()
    n = (needle or "").lower().strip()
    if not n:
        return False
    if re.search(r"\w", n) and " " not in n:
        return re.search(rf"(?<!\w){re.escape(n)}(?!\w)", h) is not None
    return n in h


def terminology_report(rows: list[dict], terms: dict, max_examples: int = 3) -> list[dict]:
    """For each glossary term EN→RU, among translated strings whose original contains EN, count
    those whose translation is missing RU (the term wasn't applied). Returns a list sorted by
    violation count desc: [{term, expected, total, violations, examples:[{original,translation}]}]."""
    translated = [r for r in rows
                  if r.get("status") == "translated" and (r.get("translation") or "").strip()]
    report = []
    for en, value in (terms or {}).items():
        forms = [f.lower().strip() for f in accepted_forms(value)]
        if not en or not forms:
            continue
        matching = [r for r in translated if _contains_word(r.get("original") or "", en)]
        if not matching:
            continue
        # The EXPECTED term is checked by substring, not whole-word: Russian inflects names
        # (Вайтран → Вайтрана/Вайтране), so the stem appearing anywhere means it was applied.
        violations = [r for r in matching
                      if not any(f in (r.get("translation") or "").lower() for f in forms)]
        if violations:
            report.append({
                "term": en, "expected": canonical(value),
                "total": len(matching), "violations": len(violations),
                "examples": [{"original": v.get("original"), "translation": v.get("translation")}
                             for v in violations[:max_examples]],
            })
    report.sort(key=lambda x: x["violations"], reverse=True)
    return report


def terminology_summary(rows: list[dict], terms: dict) -> dict:
    """Compact roll-up for the UI: how many glossary terms have inconsistencies and the total
    number of violating strings, plus the per-term report."""
    rep = terminology_report(rows, terms)
    return {
        "terms_with_issues": len(rep),
        "total_violations":  sum(r["violations"] for r in rep),
        "report":            rep,
    }


# ── Enforcement ───────────────────────────────────────────────────────────────
#
# The glossary was injected into every prompt and never checked afterwards. On the live
# collection that let 352 strings through in which "Skyrim" had become "Сиродил" — a
# different province — and nothing anywhere marked them as suspect. Detection after the
# fact is not enough: a wrong proper noun has to stop a string from counting as done.
#
# Precision matters more than recall here, because a false positive sends good work to a
# human. So a term is only enforced when it is unambiguous:
#   * the English term appears as a whole word in the original;
#   * the expected Russian stem is absent from the translation;
#   * the translation is not simply the original passed through (that is already caught by
#     the quality score, and would otherwise double-report);
#   * the original is not a filename or a bare mod title, where names stay in English.

_FILENAME_RE = re.compile(r"\.(esp|esm|esl|bsa|txt|json|swf|pex|dds|nif)\b", re.I)


# A glossary entry may name more than one acceptable rendering. Skyrim's Russian has
# several, and demanding one of them reported correct work in the tens of thousands:
#
#   Magicka      «магия» is Bethesda's own word for it            2 362 strings
#   Guard        «страж» inside a name — "Honor Guard"            1 995
#   Dragonborn   «Драконорождённый» stands beside «Довакин»       1 630
#   Boots        «ботинки» is not wrong                           1 015
#   Companion    «спутник» generically, «Соратник» for the guild    897
#   Inn          «гостиница»                                        627
#
# So a value is either a string — one rendering, as before — or a list, whose first entry
# is the canonical one to put in a prompt and the rest are accepted without complaint.
# This is enforcement, not preference: the list says what is not a defect, and the first
# entry says what to ask for.

def accepted_forms(value) -> list[str]:
    """Every rendering a glossary entry accepts."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [v for v in value if isinstance(v, str) and v.strip()]
    return []


def canonical(value) -> str:
    """The rendering to ask a model for. Empty when the entry is unusable."""
    forms = accepted_forms(value)
    return forms[0] if forms else ""



# Russian inflects, and a glossary entry is one form of a word. "Железо" is the noun; a
# sword made of it is "Железный", and "Здоровье" becomes "здоровья". Matching the entry
# verbatim reports those as violations and sends correct work to a human, so the
# comparison is on the stem.
# Enforcement is deliberately narrower than reporting, because a false positive costs a
# person a review item for work that was fine. Measured against the live collection, two
# shapes produce nearly all of the noise:
#
#   * multi-word terms — "Тёмное Братство" becomes "Тёмного Братства", and matching a
#     phrase across inflected words is not something a prefix rule can do;
#   * short terms whose stem itself changes — "Замок" becomes "замка", "Еда" becomes
#     "едой". A prefix long enough to be specific no longer matches the inflected form.
#
# What is left is exactly what this is for: transliterated proper nouns and long nouns,
# where the first five characters survive declension. Skyrim, Whiterun, Solitude, Магикка.
# Everything else still appears in the on-demand report, which a person reads.
_MIN_ENFORCED_TERM = 6
_PREFIX_CHARS      = 5


# Фразу тоже можно требовать — просто не целиком.
#
# «Тёмное Братство» становится «Тёмного Братства», и искать фразу целиком бесполезно:
# склоняется каждое слово. Поэтому многословное имя было исключено из проверки вовсе — и
# это оставило реестр официальных имён работающим на 1%: из 7 030 записей проверялись 73,
# потому что почти каждое имя в русском состоит из двух слов. «Elven Battleaxe →
# Эльфийская секира» не проверялось никогда.
#
# Правильный вопрос не «стоит ли фраза целиком», а «есть ли в переводе каждое её слово».
# Стеммы слов ищутся по отдельности и в любом порядке: «Эльфийская секира» считается
# применённой в «Эльфийской секиры», «секира эльфийская» и «эльфийскую секиру».
_PHRASE_SPLIT_RE = re.compile(r"[^А-Яа-яЁё0-9]+")
# Служебные слова не несут имени: требовать «из» бессмысленно, а его отсутствие —
# не нарушение.
_RU_FUNCTION = frozenset("из в на с со от для и у к по о об до над под при за без".split())


def _phrase_words(ru: str) -> list[str]:
    """Значащие слова имени — те, по которым его можно узнать."""
    return [w for w in _PHRASE_SPLIT_RE.split((ru or "").lower())
            if len(w) >= 3 and w not in _RU_FUNCTION]


def _is_enforceable(ru: str) -> bool:
    """Достаточно ли имя определённо, чтобы его ТРЕБОВАТЬ.

    Одно слово — как прежде: не короче шести букв, иначе оно слишком общее.
    Несколько — требуется, когда хотя бы одно значащее слово достаточно длинное; фраза из
    коротких общих слов («Зал войны») требованием быть не может.
    """
    t = (ru or "").strip()
    if not t:
        return False
    if " " not in t and "-" not in t:
        return len(t) >= _MIN_ENFORCED_TERM
    words = _phrase_words(t)
    return len(words) >= 2 and any(len(w) >= _MIN_ENFORCED_TERM for w in words)


# Настоящая морфология вместо пятибуквенного префикса.
#
# _stems писался потому, что морфологии под рукой не было: пятибуквенный префикс плюс три
# правила про беглые гласные. Он честно работает на транслитерированных именах — «Скайрим»,
# «Вайтран» — и промахивается на обычных словах, где меняется не только хвост. «Ремонт
# лёгкой брони» объявлялся нарушением «Light Armor → Лёгкая броня», потому что «лёгкой» и
# «брони» не начинаются с «лёгка» и «броня».
#
# Замер: из 11 359 нарушений по стеммеру 729 — такие. Это 729 строк, которые ушли бы на
# машину переделывать верный текст.
#
# Поэтому проверяются ОБА способа, и достаточно любого. Морфология видит склонение точно,
# префикс подстраховывает там, где словаря нет: выдуманные имена модов, «Сталгримовая».
# Шире — значит меньше ложных срабатываний, а цена ложного здесь выше цены пропуска:
# пропуск ничего не стоит, ложное отправляет хорошую работу человеку или машине.
_MORPH = None
_MORPH_TRIED = False


def _morph():
    """pymorphy3, если он есть. Отсутствие выключает половину проверки, а не всю."""
    global _MORPH, _MORPH_TRIED
    if not _MORPH_TRIED:
        _MORPH_TRIED = True
        try:
            import pymorphy3
            _MORPH = pymorphy3.MorphAnalyzer()
        except Exception as exc:
            import logging
            logging.getLogger(__name__).info(
                "terminology: pymorphy3 недоступен, сверка идёт только по префиксу: %s", exc)
    return _MORPH


@lru_cache(maxsize=300_000)
def _lemma(word: str) -> frozenset:
    """ВСЕ возможные леммы слова, а не самая вероятная.

    parse()[0] берёт один разбор, и для омонимичной формы он бывает не тот: «брони» это
    и «броня», и «бронь» (бронирование), а самой вероятной pymorphy считает вторую. При
    сравнении по одной лемме «Лёгкая броня» переставала находиться в «лёгкой брони» —
    то есть морфология начинала врать ровно там, где её добавляли.

    Совпадением считается пересечение множеств: слово то же, если хоть один его разбор
    сходится с разбором искомого.
    """
    m = _morph()
    if m is None:
        return frozenset((word,))
    try:
        return frozenset(p.normal_form for p in m.parse(word)) or frozenset((word,))
    except Exception:
        return frozenset((word,))


_RU_WORD_RE = re.compile(r"[А-Яа-яЁё]+")


@lru_cache(maxsize=100_000)
def _text_lemmas(text: str) -> frozenset:
    out: set = set()
    for w in _RU_WORD_RE.findall((text or "").lower()):
        out |= _lemma(w)
    return frozenset(out)


def _lemma_satisfied(ru: str, translation: str) -> bool:
    """Каждое значащее слово имени присутствует в переводе в какой-нибудь форме."""
    if _morph() is None:
        return False
    words = _phrase_words(ru)
    if not words:
        return False
    have = _text_lemmas(translation)
    return all(_lemma(w) & have for w in words)


def _phrase_satisfied(ru: str, low_translation: str) -> bool:
    """Есть ли в переводе каждое значащее слово имени — в любой форме и любом порядке."""
    words = _phrase_words(ru)
    if not words:
        return False
    if all(any(st in low_translation for st in _stems(w)) for w in words):
        return True
    return _lemma_satisfied(ru, low_translation)

_RU_ENDINGS = ("ого", "ому", "ыми", "ими", "ая", "ое", "ые", "ый", "ий", "ой", "ом",
               "ах", "ям", "ев", "ов", "а", "я", "о", "е", "ы", "и", "у", "ю", "ь", "й")


_VOWELS = "аеёиоуыэюя"


# «ё» и «е» — одна буква для сравнения.
#
# Официальная локализация Bethesda не использует «ё» вообще, а наши переводы используют.
# Без этой нормализации «Тяжёлая броня» не совпадает с «Тяжелая броня», и глоссарий
# объявляет нарушением тот же самый текст. То же и внутри корпуса: «Чёрный» против
# «Черный».
def _fold_yo(text: str) -> str:
    return (text or "").replace("ё", "е").replace("Ё", "Е")


@lru_cache(maxsize=100_000)
def _stems(term: str) -> tuple[str, ...]:
    """Prefixes that a glossary entry's declined forms all start with.

    Conservative: only trims single words over five characters and never below five, so a
    short name stays exact rather than shrinking into something that matches half the text.

    A prefix, not a suffix-stripping rule. Russian inflection changes the tail in ways a
    fixed ending list does not cover — "Торговец" becomes "торговца", "Еда" becomes "едой"
    — and each miss reports correct work as a violation.

    One prefix is not always enough. Russian has a fleeting vowel: the last vowel of the
    stem disappears when an ending is added, so «Уровень» becomes «уровня» and «Камень»
    becomes «камня». A prefix taken off the nominative reads «урове», which no oblique
    form starts with, and every one of them was reported. 590 strings on the live
    collection were that word alone. So the syncopated prefix is a candidate too.
    """
    t = _fold_yo((term or "").lower().strip())
    if " " in t:
        return (t,)                   # multi-word terms are matched whole (report only)
    out = [t[:max(_PREFIX_CHARS, len(t) - 2)]]
    # «уровень» → «уровн»: drop the vowel before the final consonant, then cut the ending.
    # Four characters is enough here where five is the floor above, because this prefix
    # ends in a consonant cluster — «камн» belongs to камня/камне/камнем and to nothing
    # else, while a four-letter prefix cut from the front of a word need not.
    # Only for a nominative in ь/й. Applied to a word ending in a vowel it invents
    # things: «булава» came out as «булв», which belongs to no form of the word.
    if len(t) >= 5 and t[-1] in "ьй" and t[-2] not in _VOWELS and t[-3] in _VOWELS:
        syncopated = t[:-3] + t[-2]
        if len(syncopated) >= 4:
            out.append(syncopated)
    # The same vowel drops in a nominative that ends in a consonant: «Свиток» becomes
    # «свитка», «свитков»; «Замок» becomes «замка». The prefix cut from the nominative is
    # «свито», which no oblique form starts with, and 105 strings were held for that one
    # word. Only when what is left ends in a consonant cluster — otherwise this is just
    # the prefix rule again with a letter missing.
    if (len(t) >= 5 and t[-1] not in _VOWELS + "ьй"
            and t[-2] in _VOWELS and t[-3] not in _VOWELS):
        syncopated = t[:-2] + t[-1]
        if len(syncopated) >= 4:
            out.append(syncopated)
    # A noun ending in a vowel declines by replacing it: «Магия» → магии, магию, магией.
    # The five-character floor above leaves a five-letter word untrimmed, so «магия» was
    # required verbatim and «Укрепление магии» read as a violation — 2 362 strings. This
    # prefix can be one character shorter because it is the whole word bar its ending,
    # not an arbitrary cut. It can match a longer relative — «маги» is also the start of
    # «магистр» — and that is the right way to be wrong: a missed violation costs
    # nothing, a false one costs somebody a review.
    if len(t) >= 5 and t[-1] in "ьй" + _VOWELS:
        shortened = t[:-1]
        if len(shortened) >= 4:
            out.append(shortened)
    return tuple(dict.fromkeys(out))


_TOKEN_RE = re.compile(r"<[^>]*>|\{[^}]*\}|\[[A-Za-z][^\]]*\]|%\w+")


def _strip_tokens(text: str) -> str:
    """Remove game tokens so their contents are not read as translatable words."""
    return _TOKEN_RE.sub(" ", text or "")


def _is_untranslatable_name(original: str) -> bool:
    """Filenames and plugin names keep their English form; a glossary hit there is noise."""
    return bool(_FILENAME_RE.search(original or ""))


# The curated glossary is 204 entries and a linear scan over it costs nothing. The
# registry of official vanilla names is 14 168, and a scan over that is 6.5 billion
# word-searches across a recompute of the whole collection — minutes become days.
#
# So: an index from the first word of a term to the terms starting with it. A term can
# only be present if its first word is, which makes the index a superset filter — the
# per-term test below is unchanged and decides exactly as it did, it is simply not asked
# about terms the source cannot contain.
_INDEX_CACHE: dict[int, dict[str, tuple[str, ...]]] = {}
_INDEX_MIN_TERMS = 400          # ниже этого перебор дешевле, чем индекс
_WORD_SPLIT_RE = re.compile(r"[^A-Za-z0-9']+")


def _first_word(term: str) -> str:
    parts = _WORD_SPLIT_RE.split((term or "").strip().lower(), 1)
    return parts[0] if parts else ""


def _term_index(terms: dict):
    """{первое слово: (позиция, термин, значение)} — или None, когда словарь мал.

    Позиция хранится, чтобы порядок результата не зависел от того, какое слово источника
    нашлось первым: `req_terms` берёт первые три нарушения, и порядок в них должен быть
    порядком словаря, как при переборе.
    """
    if len(terms) < _INDEX_MIN_TERMS:
        return None
    key = id(terms)
    cached = _INDEX_CACHE.get(key)
    if cached is not None:
        return cached
    index: dict[str, list[tuple[int, str, object]]] = {}
    for pos, (en, value) in enumerate(terms.items()):
        fw = _first_word(en)
        if fw:
            index.setdefault(fw, []).append((pos, en, value))
    built = {k: tuple(v) for k, v in index.items()}
    _INDEX_CACHE.clear()             # один словарь за раз; кеш не должен расти молча
    _INDEX_CACHE[key] = built
    return built


def _candidate_terms(original: str, terms: dict):
    """Термины, которые вообще могут стоять в этом источнике, в порядке словаря."""
    index = _term_index(terms)
    if index is None:
        return terms.items()
    words = {w for w in _WORD_SPLIT_RE.split((original or "").lower()) if w}
    found: list[tuple[int, str, object]] = []
    for w in words:
        found.extend(index.get(w, ()))
    found.sort(key=lambda t: t[0])
    return [(en, value) for _pos, en, value in found]


# Ключи, пришедшие из реестра официальных имён, а не из курируемого глоссария.
#
# Разница в том, ГДЕ их можно требовать, и она существенная. Реестр — это имена:
# «Shock Damage → Урон электричеством» верно как название эффекта и неверно внутри
# описания заклинания, где по-русски пишут «наносит урона молнией». Курируемая запись
# вроде Skyrim или Dwemer — имя собственное, и требовать её можно везде.
#
# Без этого разделения реестр давал 1 205 «нарушений» на 20 000 строк, и заметная часть
# была придиркой к правильной прозе.
#
# Признак живёт на самом словаре, а не в глобальной переменной: глобальная делала
# поведение зависимым от того, звал ли кто-то раньше load_terms, и один тест начинал
# менять результат другого.


class TermSet(dict):
    """Словарь терминов, помнящий, какие из них пришли из реестра имён."""

    __slots__ = ("registry",)

    def __init__(self, *a, registry=(), **kw):
        super().__init__(*a, **kw)
        self.registry = frozenset(registry)

# Типы записей, у которых FULL — это имя. Список держится здесь, а не берётся из
# consistency.py, чтобы проверка терминологии от неё не зависела.
_NAME_RECORDS = frozenset(("NPC_", "LCTN", "CELL", "WEAP", "ARMO", "ALCH", "MISC", "INGR",
                           "BOOK", "QUST", "SPEL", "KEYM", "AMMO", "ENCH", "MGEF", "ACTI"))


def _is_name_field(rec_type, field_type) -> bool:
    return field_type == "FULL" and (rec_type or "") in _NAME_RECORDS


def glossary_violations(original: str, translation: str, terms: dict,
                        rec_type: str | None = None,
                        field_type: str | None = None) -> list[tuple[str, str]]:
    """Glossary terms present in `original` whose expected translation is missing.

    `rec_type` / `field_type` решают, требовать ли имя из реестра. Без них реестр молчит:
    назвать «урона молнией» нарушением в описании заклинания — это не проверка, а её
    порча, а отличить описание от названия предмета по одному тексту нельзя.

    Returns [(english_term, expected_russian), ...], empty when the translation is clean,
    the inputs are empty, or the string is a name that should stay in English.
    """
    if not original or not translation or not terms:
        return []
    if original.strip() == translation.strip():
        return []                      # untranslated passthrough — a different problem
    if _is_untranslatable_name(original):
        return []
    # Game tokens are copied verbatim by design — <Alias=Jarl> is a runtime placeholder,
    # not the word "Jarl". Matching inside one reports a violation for text the translator
    # was never allowed to touch.
    registry = getattr(terms, "registry", frozenset())
    original = _strip_tokens(original)
    low_translation = _fold_yo(_strip_tokens(translation).lower())
    out = []
    for en, value in _candidate_terms(original, terms):
        forms = accepted_forms(value)
        if not en or not forms:
            continue
        # Being specific enough to DEMAND and being able to SATISFY are two different
        # questions, and conflating them is what kept «Магия» from counting. A five-letter
        # word is too ambiguous to require — but when the translation contains it, the
        # term was applied, and the entry is satisfied whatever its length.
        if not any(_is_enforceable(f) for f in forms):
            continue
        if en in registry and not _is_name_field(rec_type, field_type):
            continue               # имя из реестра требуется только в поле имени
        if not _contains_word(original, en):
            continue
        if any(_phrase_satisfied(f, low_translation) if (" " in f or "-" in f)
               else (any(st in low_translation for st in _stems(f))
                     or _lemma_satisfied(f, low_translation))
               for f in forms):
            continue
        out.append((en, canonical(value)))
    return out


def audit_stored(repo, terms: dict, mod_name: str | None = None,
                 apply: bool = False, limit: int | None = None) -> dict:
    """Re-validate translations already stored, and optionally send the bad ones to review.

    The write gate only protects what is written from now on. Everything saved before a
    check existed was never subjected to it — on the live collection that is a hundred
    thousand strings, several thousand of which break a glossary term and several hundred
    more of which have markup rewritten into look-alike characters that renders as junk
    in-game.

    Every rule is re-run, through the same compute_string_status the write gate uses, so an
    audit and a fresh save cannot disagree.

    apply=False reports without touching anything, which is the safe default: the report is
    worth reading before thousands of strings move into someone's review queue.
    """
    sql = ("SELECT id, mod_name, original, translation FROM strings "
           "WHERE status='translated' AND translation != ''")
    params: tuple = ()
    if mod_name:
        sql += " AND mod_name=?"
        params = (mod_name,)
    if limit:
        sql += f" LIMIT {int(limit)}"

    from translator.validation.quality import compute_string_status

    checked = 0
    by_term: dict[str, int] = {}
    by_mod: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    offenders: list[int] = []
    examples: list[dict] = []
    for r in repo.db.execute(sql, params).fetchall():
        checked += 1
        original    = r["original"] or ""
        translation = r["translation"] or ""
        # Re-run every rule, not only the glossary: a dropped game token or markup rewritten
        # with look-alike brackets renders as junk in-game, and both were stored as finished
        # work before the checks existed. compute_string_status is the same judgement the
        # write gate applies, so a re-audit and a fresh save agree by construction.
        _qs, _tok_ok, issues, status = compute_string_status(original, translation, terms)
        if status == "translated":
            continue
        offenders.append(r["id"])
        by_mod[r["mod_name"]] = by_mod.get(r["mod_name"], 0) + 1
        if not issues:
            # No named problem — the quality score alone put it here.
            by_kind["low_score"] = by_kind.get("low_score", 0) + 1
        for issue in issues:
            kind = "glossary" if issue.startswith("glossary:") else (
                   "markup" if "markup" in issue or "angle brackets" in issue else "token")
            by_kind[kind] = by_kind.get(kind, 0) + 1
        for en, _ru in glossary_violations(original, translation, terms):
            by_term[en] = by_term.get(en, 0) + 1
        if len(examples) < 20:
            examples.append({"mod": r["mod_name"], "original": original[:120],
                             "translation": translation[:120], "issues": issues[:3]})

    moved = 0
    if apply and offenders:
        for i in range(0, len(offenders), 500):
            chunk = offenders[i:i + 500]
            ph = ",".join("?" * len(chunk))
            repo.db.execute(
                f"UPDATE strings SET status='needs_review' WHERE id IN ({ph})", tuple(chunk))
            moved += len(chunk)
        repo.db.commit()

    return {
        "checked": checked,
        "violations": len(offenders),
        "moved_to_review": moved,
        "by_kind": dict(sorted(by_kind.items(), key=lambda kv: -kv[1])),
        "by_term": dict(sorted(by_term.items(), key=lambda kv: -kv[1])[:25]),
        "by_mod": dict(sorted(by_mod.items(), key=lambda kv: -kv[1])[:25]),
        "examples": examples,
    }


# ── Один загрузчик на всех ────────────────────────────────────────────────────
#
# Глоссарий грузился в трёх местах — в воротах записи, в пересчёте и в сборке джоба —
# тремя копиями одного и того же кода. Это ровно тот узор, который уже однажды стоил
# двух копий compute_string_status, расходившихся год.
#
# Источников теперь два, и они разной природы:
#
#   data/skyrim_terms.json    204 записи, курируемые вручную, со списками допустимых
#                             форм. Это предпочтения проекта.
#   data/vanilla_names.json   7 030 имён из официальной локализации Skyrim. Это не
#                             предпочтение, а то, что игрок видит в базовой игре, и
#                             голосовать тут не о чем.
#
# Курируемый выигрывает при совпадении ключа: он знает про несколько допустимых форм
# («Магия» и «Магикка»), а реестр знает одну.

_TERMS_CACHE: dict[str, dict] = {}


def load_terms(curated_path=None, vanilla_path=None, use_cache: bool = True) -> dict:
    """Глоссарий и реестр официальных имён, слитые в один словарь.

    Отсутствующий файл выключает свою половину, а не роняет вызывающего: проверка
    терминологии должна деградировать в тишину, а не в исключение посреди записи.
    """
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    curated = Path(curated_path) if curated_path else root / "data" / "skyrim_terms.json"
    vanilla = Path(vanilla_path) if vanilla_path else root / "data" / "vanilla_names.json"
    key = f"{curated}|{vanilla}"
    if use_cache and key in _TERMS_CACHE:
        return _TERMS_CACHE[key]

    merged: dict = {}
    registry_keys: set[str] = set()
    for path, what in ((vanilla, "реестр официальных имён"), (curated, "глоссарий")):
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    if path == vanilla:
                        registry_keys.update(data)
                    else:
                        # Ключ, который есть в обоих, курируемый: у него список форм,
                        # и требовать его можно везде, как любое имя собственное.
                        registry_keys.difference_update(data)
                    merged.update(data)          # курируемый читается вторым и выигрывает
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                "terminology: %s не загружен (%s): %s", what, path.name, exc)
    # Записи, которые нельзя требовать в прозе. Различить термин с закреплённой
    # передачей от обычного слова можно только замером: для каждой записи считается,
    # сколько раз официальная таблица её подтверждает и сколько раз противоречит.
    # «Stalhrim» — 479 подтверждений против 0, это термин. «Gold» — 96 против 172, и
    # требовать «Золото» в прозе значит браковать «золотой», «позолота», «деньги».
    # Такие записи опускаются до уровня реестра: они спрашиваются только в поле имени.
    #
    # Замер живёт в scratchpad/term_reliability.py; результат — в файле, потому что
    # пересчитывать его на каждой загрузке незачем.
    try:
        only_names = root / "data" / "terms_name_field_only.json"
        if only_names.exists():
            listed = json.loads(only_names.read_text(encoding="utf-8"))
            if isinstance(listed, list):
                registry_keys.update(t for t in listed if t in merged)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning(
            "terminology: список «только поле имени» не прочитан: %s", exc)
    merged = TermSet(merged, registry=registry_keys)
    if use_cache:
        _TERMS_CACHE.clear()
        _TERMS_CACHE[key] = merged
    return merged
