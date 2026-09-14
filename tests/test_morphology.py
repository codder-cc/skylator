"""
Настоящая морфология вместо пятибуквенного префикса.

_stems писался потому, что морфологии под рукой не было: префикс в пять букв плюс три
правила про беглые гласные. На транслитерированных именах — «Скайрим», «Вайтран» — он
работает честно. На обычных словах промахивается, потому что меняется не только хвост:

    «Ремонт лёгкой брони»  объявлялся нарушением «Light Armor → Лёгкая броня»
    «Смертельные раны»     объявлялись нарушением «Mortal Wound → Смертельная рана»

Замер на корпусе: 11 359 нарушений по стеммеру, 8 980 после добавления морфологии.
2 379 строк, которые уехали бы на машину переделывать верный текст.

Проверяются ОБА способа, и хватает любого. Морфология точна на склонении, префикс
подстраховывает там, где словаря нет — выдуманные имена модов. Шире значит меньше ложных
срабатываний, а цена ложного здесь выше цены пропуска: пропуск не стоит ничего, ложное
отправляет хорошую работу переделывать.
"""
import pytest

from translator.validation.terminology import (
    _lemma, _lemma_satisfied, _morph, glossary_violations,
)

pytestmark = pytest.mark.skipif(_morph() is None, reason="pymorphy3 не установлен")


# ── что морфология разрешает, а префикс нет ──────────────────────────────────

@pytest.mark.parametrize("term, translation", [
    ("Лёгкая броня", "Ремонт лёгкой брони"),
    ("Смертельная рана", "Смертельные раны"),
    ("Железная руда", "Слиток из железной руды"),
    ("Серебряная рука", "Логово Серебряной руки"),
    ("Тёмное Братство", "Убежище Тёмного Братства"),
])
def test_declension_is_not_a_violation(term, translation):
    assert _lemma_satisfied(term, translation)


@pytest.mark.parametrize("word, form", [
    ("вода", "воде"), ("вода", "водой"), ("секира", "секиры"),
    ("камень", "камня"), ("замок", "замка"), ("еда", "едой"),
    ("Скайрим", "Скайриме"), ("белая", "белого"),
    ("броня", "брони"),   # омонимия: «брони» это ещё и «бронь»
])
def test_the_forms_of_one_word_share_a_lemma(word, form):
    assert _lemma(word) & _lemma(form)


# ── что морфология НЕ должна разрешать ───────────────────────────────────────

@pytest.mark.parametrize("word, other", [
    ("вода", "водный"),      # словообразование, а не склонение
    ("двемер", "двемерский"),
])
def test_derivation_is_a_different_word(word, other):
    assert not (_lemma(word) & _lemma(other))


def test_a_missing_word_is_still_a_violation():
    assert not _lemma_satisfied("Лёгкая броня", "Тяжёлый шлем")


def test_the_check_still_catches_a_real_one():
    """Скайрим → Сиродил — другая провинция, и морфология этого не прощает."""
    assert glossary_violations("Travel across Skyrim", "Путешествие по Сиродилу",
                               {"Skyrim": "Скайрим"})


# ── отсутствие словаря выключает половину, а не всю проверку ─────────────────

def test_without_pymorphy_the_prefix_still_works(monkeypatch):
    import translator.validation.terminology as T
    monkeypatch.setattr(T, "_MORPH", None)
    monkeypatch.setattr(T, "_MORPH_TRIED", True)
    assert T._lemma_satisfied("Лёгкая броня", "лёгкой брони") is False
    # префикс по-прежнему ловит настоящее нарушение
    assert T.glossary_violations("Travel across Skyrim", "Путешествие по Сиродилу",
                                 {"Skyrim": "Скайрим"})
