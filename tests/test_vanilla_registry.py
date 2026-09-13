"""
Официальная локализация как таблица авторитета, и почему реестр пришлось сузить трижды.

Игра поставляется со всеми языками. Выравнивание английской и русской таблиц по id строк
даёт 25 984 пары EN→RU — тот текст, который русский игрок видит в базовой игре.

Соблазн — влить их в глоссарий целиком. Замер сказал, что будет:

    на 20 000 строк «нарушений»    5 379
        Damage → «Повреждение:»      550     ARMOR → «ДОСПЕХИ»   268
        Right  → «Вправо»            223     Name  → «Название»  129

Требовать «Повреждение» в описании заклинания, где верно «урон», — это порча проверки,
а не проверка. Одиночное общее слово по форме неотличимо от имени: «Damage» и
«Dragonsreach» устроены одинаково.

Три сужения, каждое по замеру:

    1. минимум два слова, и русский не кончается двоеточием    5 379 → 76
    2. остаток — интерфейсные фразы: «TO STEAL» ловится внутри «to steal» в прозе
    3. спросить корпус: имя — это текст, который у нас самих стоит в поле FULL
       записи-имени                                                  76 → 17

И оставшиеся 17 — настоящие: The Warrens → «Муравейник», Tundra Cotton → «Пушица»,
The Arcanaeum → «Арканеум», Bone Breaker → «Костолом».
"""
import json

import pytest

from scripts.vanilla_holdout import HOLDOUT_SHARE, in_holdout, is_name
from translator.validation.terminology import glossary_violations, load_terms


# ── что считается именем ─────────────────────────────────────────────────────

@pytest.mark.parametrize("en, ru", [
    ("Mountain Flower", "Горноцвет"),
    ("The Warrens", "Муравейник"),
    ("Tundra Cotton", "Пушица"),
    ("Bone Breaker", "Костолом"),
])
def test_a_compound_name_belongs_in_the_registry(en, ru):
    assert is_name(en, ru)


@pytest.mark.parametrize("en, ru, why", [
    ("Damage", "Повреждение:", "одиночное общее слово, да ещё подпись"),
    ("Name", "Название", "интерфейсная подпись"),
    ("Dragonsreach", "Драконий Предел", "имя, но одиночное — приходит точным совпадением"),
    ("Place", "Поместить:", "двоеточие выдаёт подпись"),
    ("I don't have time for this.", "Мне некогда.", "реплика, а не имя"),
    ("A", "А", "слишком коротко"),
])
def test_what_must_not_get_in(en, ru, why):
    assert not is_name(en, ru), why


# ── holdout ──────────────────────────────────────────────────────────────────

def test_the_split_is_deterministic_and_survives_a_rebuild():
    """Разбиение по хешу источника: пересборка реестра не перемешивает срез."""
    assert in_holdout("Dragonsreach") == in_holdout("Dragonsreach")
    assert in_holdout("The Bee and Barb") == in_holdout("The Bee and Barb")


def test_the_holdout_is_about_a_tenth():
    sample = [f"Test String {i}" for i in range(4000)]
    share = sum(1 for s in sample if in_holdout(s)) / len(sample)
    assert 1 / HOLDOUT_SHARE * 0.7 < share < 1 / HOLDOUT_SHARE * 1.3


def test_nothing_in_the_holdout_reached_the_registry(tmp_path):
    """Единственная необратимая ошибка в этом порядке — влить срез в систему и потом
    замерять на нём модель: замер покажет размер нашей памяти, а не качество модели."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    holdout_path = root / "data" / "vanilla_holdout.json"
    names_path = root / "data" / "vanilla_names.json"
    if not holdout_path.exists() or not names_path.exists():
        pytest.skip("реестр ещё не собран")
    holdout = json.loads(holdout_path.read_text(encoding="utf-8"))
    names = json.loads(names_path.read_text(encoding="utf-8"))
    assert holdout, "срез пуст — значит его не резали"
    assert not (set(holdout) & set(names)), "срез протёк в реестр"


# ── один загрузчик ───────────────────────────────────────────────────────────

def test_the_curated_glossary_wins_over_the_registry(tmp_path):
    """У курируемой записи есть список допустимых форм, у реестра — одна. При
    совпадении ключа побеждает та, что знает больше."""
    curated = tmp_path / "curated.json"
    vanilla = tmp_path / "vanilla.json"
    curated.write_text(json.dumps({"Magicka": ["Магия", "Магикка"]}), encoding="utf-8")
    vanilla.write_text(json.dumps({"Magicka": "Магикка", "The Warrens": "Муравейник"}),
                       encoding="utf-8")
    terms = load_terms(curated, vanilla, use_cache=False)
    assert terms["Magicka"] == ["Магия", "Магикка"]
    assert terms["The Warrens"] == "Муравейник"


def test_a_missing_file_turns_its_half_off_rather_than_failing(tmp_path):
    terms = load_terms(tmp_path / "нет.json", tmp_path / "тоже нет.json", use_cache=False)
    assert terms == {}


# ── что это даёт проверке ────────────────────────────────────────────────────

def test_the_registry_demands_the_official_name():
    terms = {"The Warrens": "Муравейник"}
    assert glossary_violations("Leave the Warrens.", "Уйти из Подземелий Маркарта.", terms)
    assert glossary_violations("Leave the Warrens.", "Уйти из Муравейника.", terms) == []


def test_the_index_gives_the_same_answer_as_a_scan():
    """Индекс по первому слову — фильтр-надмножество, а не другое правило. На 1 500
    строках корпуса перебор и индекс совпали 1 500 раз при ускорении в тысячу раз."""
    import translator.validation.terminology as T
    terms = {f"Fake Name {i}": f"Имя{i}" for i in range(600)}
    terms["The Warrens"] = "Муравейник"
    src, dst = "Leave the Warrens now.", "Уйти из Подземелий."
    T._INDEX_CACHE.clear()
    with_index = glossary_violations(src, dst, terms)
    old = T._INDEX_MIN_TERMS
    try:
        T._INDEX_MIN_TERMS = 10 ** 9
        scanned = glossary_violations(src, dst, terms)
    finally:
        T._INDEX_MIN_TERMS = old
        T._INDEX_CACHE.clear()
    assert with_index == scanned


# ── фраза требуется по словам, а не целиком ──────────────────────────────────
#
# Многословное имя было исключено из проверки вовсе: «Тёмное Братство» становится
# «Тёмного Братства», и фраза целиком не находится. Это оставило реестр работающим на
# 1% — 73 записи из 7 030, потому что почти каждое имя в русском из двух слов.
# «Elven Battleaxe → Эльфийская секира» не проверялось никогда.

T_PHRASE = {"Elven Battleaxe": "Эльфийская секира"}


def test_a_two_word_name_is_now_enforced():
    from translator.validation.terminology import _is_enforceable
    assert _is_enforceable("Эльфийская секира")


@pytest.mark.parametrize("ru", [
    "Эльфийской секиры пламени",   # склонение обоих слов
    "Секира эльфийская",           # другой порядок
    "эльфийскую секиру",           # винительный
])
def test_declension_and_word_order_are_not_violations(ru):
    assert glossary_violations("Elven Battleaxe", ru, T_PHRASE, "WEAP", "FULL") == []


def test_the_old_name_is_a_violation():
    assert glossary_violations("Elven Battleaxe of Flames",
                               "Эльфийский Боевой Топор Пламени",
                               T_PHRASE, "WEAP", "FULL")


def test_a_phrase_of_short_common_words_is_not_specific_enough():
    from translator.validation.terminology import _is_enforceable
    assert not _is_enforceable("Зал войны")


# ── реестр требуется только в поле имени ─────────────────────────────────────
#
# «Shock Damage → Урон электричеством» верно как название эффекта и неверно внутри
# описания заклинания, где по-русски пишут «наносит урона молнией». Без этого
# разделения реестр давал 1 205 придирок на 20 000 строк.

def _registry(**pairs):
    """Словарь терминов, помеченный как пришедший из реестра имён."""
    from translator.validation.terminology import TermSet
    return TermSet(pairs, registry=pairs)


def test_a_registry_name_binds_in_a_name_field():
    terms = _registry(**{"Shock Damage": "Урон электричеством"})
    assert glossary_violations("Shock Damage", "Урон молнией", terms, "MGEF", "FULL")


def test_the_same_name_is_silent_in_prose():
    terms = _registry(**{"Shock Damage": "Урон электричеством"})
    assert glossary_violations(
        "Lightning strikes, dealing shock damage to Health",
        "Молния поражает, нанося урона молнией по здоровью", terms, "MGEF", "DNAM") == []


def test_without_a_field_the_registry_stays_quiet():
    """Отличить описание от названия по одному тексту нельзя, поэтому молчание."""
    terms = _registry(**{"Shock Damage": "Урон электричеством"})
    assert glossary_violations("Shock Damage", "Урон молнией", terms) == []


def test_a_curated_entry_binds_everywhere():
    """Skyrim — имя собственное; оно не из реестра и требуется в любом поле."""
    terms = load_terms()
    assert "Skyrim" not in getattr(terms, "registry", frozenset())
    assert glossary_violations("Travel across Skyrim", "Путешествие по Сиродилу",
                               {"Skyrim": "Скайрим"})


def test_the_marker_lives_on_the_dictionary_not_in_a_global():
    """Глобальная переменная делала поведение зависимым от того, звал ли кто-то раньше
    load_terms, и один тест начинал менять результат другого."""
    plain = {"Shock Damage": "Урон электричеством"}
    assert glossary_violations("Shock Damage", "Урон молнией", plain, "MGEF", "FULL")
    assert glossary_violations("Shock Damage", "Урон молнией", plain) 
