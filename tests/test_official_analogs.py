"""Похожие записи официального перевода к строке, которой в таблице нет."""
from translator.validation import official_context as oc

TABLE = {
    "Ring of Major Health": "Кольцо настоящего здоровья",
    "Hide Armor of Major Health": "Сыромятная броня настоящего здоровья",
    "Necklace of Health": "Ожерелье здоровья",
    "Weapons can be improved at a Grindstone.": "Оружие можно улучшить на точильном камне.",
    "Grindstone Key": "Ключ от точила",
    "Iron Sword": "Железный меч",
}


def _use(monkeypatch, table=TABLE):
    monkeypatch.setattr(oc, "_table", lambda: table)
    oc._analog_index.cache_clear()


def test_the_convention_is_shown_by_neighbours(monkeypatch):
    _use(monkeypatch)
    got = dict(oc.analogs_in("Necklace of Major Health", "ARMO"))
    assert got.get("Ring of Major Health") == "Кольцо настоящего здоровья"
    oc._analog_index.cache_clear()


def test_an_exact_match_is_not_an_analog(monkeypatch):
    # Точное совпадение — дело ворот записи; на отложенной выборке это была бы утечка.
    _use(monkeypatch)
    assert "Iron Sword" not in dict(oc.analogs_in("Iron Sword", "WEAP"))
    oc._analog_index.cache_clear()


def test_a_label_gets_labels_not_sentences(monkeypatch):
    _use(monkeypatch)
    got = dict(oc.analogs_in("Grindstone", "MGEF"))
    assert "Weapons can be improved at a Grindstone." not in got
    oc._analog_index.cache_clear()


def test_dialogue_gets_none(monkeypatch):
    _use(monkeypatch)
    assert oc.analogs_in("Ring of Health", "INFO") == []
    assert oc.analog_block("Ring of Health", "DIAL") == ""
    oc._analog_index.cache_clear()


def test_the_block_reads_as_an_instruction(monkeypatch):
    _use(monkeypatch)
    b = oc.analog_block("Amulet of Major Health", "ARMO")
    assert b.startswith("Similar entries in the official game translation")
    assert "настоящего здоровья" in b
    oc._analog_index.cache_clear()
