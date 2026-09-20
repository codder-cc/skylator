"""Карточка персонажа: пол, манера речи и собственный словарь.

Обе тонкие части здесь были сделаны неправильно с первого раза, и обе ошибались молча —
карточка строилась, выглядела правдоподобно и несла в промпт чушь. Поэтому они закрыты
тестами, а не чтением глазами.
"""
from __future__ import annotations

from translator.characters import cards as C


def _meta(sex=None, race="", shared=False, names=("Берг",)):
    return {"sex": sex, "race": race, "shared": shared,
            "names": list(names), "edids": ["Bergrisar"]}


BERG = [
    ("Berg try save froma, friend. Him use metal club to smack behind.",
     "Берг пытаться спасти фрома, друг. Он использовать металлическая дубинка."),
    ("Jo'tun have many word for moh'roktha, one called mammoth.",
     "У Йотун много слов для мох'рокта, одно называется мамонт."),
    ("Berg no like. Ta'lak moh'roktha. She grunt like mammoth.",
     "Берг не любить. Та'лак мох'рокта. Она хрюкать как мамонт."),
    ("Jo'tun, Giant, give no name. Ha'mari, Man, give name many moons ago.",
     "Йотун, Гигант, не давать имя. Ха'мари, Человек, дать имя много лун назад."),
    ("Berg born Jo'tun. Clan maybe treat Berg bad.",
     "Берг рождён Йотун. Племя может обращаться с Берг плохо."),
    ("Jo'tun not trust round-ear like Berg.", "Йотун не доверять круглоухий как Берг."),
    ("Berg think moh'roktha is family.", "Берг думать мох'рокта есть семья."),
    ("Some Jo'tun clan color rock in blood.", "Некоторый Йотун клан красить камень кровь."),
    ("Berg no want fight Jo'tun.", "Берг не хотеть драться Йотун."),
]

PLAIN = [
    ("I have been waiting for you at the inn, as we agreed last night.",
     "Я ждал тебя в таверне, как мы договорились прошлой ночью."),
    ("It is a fine morning, and the roads are clear of bandits.",
     "Прекрасное утро, и дороги свободны от бандитов."),
    ("She has already spoken to the Jarl about the matter.",
     "Она уже говорила с ярлом об этом деле."),
    ("I would not go there if I were you; the cave is dangerous.",
     "Я бы не пошёл туда на твоём месте: пещера опасна."),
    ("They are saying the dragon was seen near the watchtower.",
     "Говорят, дракона видели возле сторожевой башни."),
    ("Do you have anything to trade? I am looking for a sword.",
     "У тебя есть что-нибудь на обмен? Я ищу меч."),
    ("He will be back before nightfall, I am certain of it.",
     "Он вернётся до заката, я в этом уверен."),
    ("We have not had this much trouble since the war ended.",
     "У нас не было столько хлопот с конца войны."),
    ("That is the last thing I would have expected to hear.",
     "Это последнее, что я ожидал услышать."),
]


# ── пол ───────────────────────────────────────────────────────────────────────


def test_the_card_states_the_gender_for_first_person_forms():
    card = C.build({"v": _meta(sex="f")}, {"v": PLAIN})["v"]
    assert "feminine" in card.prompt_block()


def test_no_gender_means_nothing_is_claimed():
    # Приписать род наугад хуже, чем промолчать: неверный род ломает текст.
    block = C.build({"v": _meta(sex=None)}, {"v": PLAIN})["v"].prompt_block()
    assert "masculine" not in block and "feminine" not in block


# ── манера речи ───────────────────────────────────────────────────────────────


def test_a_pidgin_speaker_is_marked_so_the_grammar_is_left_alone():
    # Реплики Берга сломаны намеренно, и грамотный русский на их месте — ошибка
    # перевода, которой не видит ни одно правило: текст грамотен, оценка 100.
    card = C.build({"v": _meta(sex="m")}, {"v": BERG})["v"]
    assert card.pidgin
    assert "do not correct the grammar" in card.prompt_block()


def test_an_ordinary_speaker_is_not_marked_as_broken():
    card = C.build({"v": _meta(sex="m")}, {"v": PLAIN})["v"]
    assert not card.pidgin


def test_too_few_lines_decide_nothing_about_the_manner():
    card = C.build({"v": _meta(sex="m")}, {"v": BERG[:3]})["v"]
    assert not card.pidgin


# ── собственный словарь ───────────────────────────────────────────────────────


def test_the_speakers_invented_words_are_collected():
    vocab = C.build({"v": _meta(sex="m")}, {"v": BERG})["v"].vocab
    assert "jo'tun" in vocab and "moh'roktha" in vocab


def test_a_frequent_ordinary_word_is_not_taken_for_a_name():
    # Первая версия выдавала Ремиэль «scrap» и «gore» как имена её языка: это её частые
    # слова, но переводить их как имена собственные нельзя. Имя отличается тем, что
    # пишется с заглавной не в начале предложения.
    lines = [("I need more scrap for the automaton.",
              "Мне нужно больше хлама для автоматона.")] * 6
    vocab = C.build({"v": _meta(sex="f")}, {"v": lines})["v"].vocab
    assert "scrap" not in vocab and "automaton" not in vocab


def test_a_shared_voice_gets_no_vocabulary():
    # За общим голосом стоят сотни персонажей, и «своё» слово там принадлежит не
    # говорящему, а моду.
    assert C.build({"v": _meta(sex="m", shared=True)}, {"v": BERG})["v"].vocab == {}


def test_a_possessive_is_the_same_name():
    lines = [("Berg's club is heavy. Berg carry it.", "Дубина Берга тяжёлая.")] * 4
    vocab = C.build({"v": _meta(sex="m")}, {"v": lines})["v"].vocab
    assert "berg's" not in vocab


# ── русское написание имени ───────────────────────────────────────────────────


def test_the_russian_spelling_is_recognised_by_sound_not_by_frequency():
    # Первая версия брала самый частый корень в строке и выдавала «olga = мальч»,
    # «skevragg = как» — то есть просто частотное русское слово. Имя собственное из
    # выдуманного языка транслитерируют, поэтому его можно узнать по звучанию.
    vocab = C.build({"v": _meta(sex="m")}, {"v": BERG})["v"].vocab
    assert vocab.get("moh'roktha", "").startswith("мох")


def test_two_spellings_of_one_name_leave_it_undecided():
    # «Йотун» и «Джотун» — один и тот же Jo'tun двумя школами; такое сводится. А вот
    # когда коллекция пишет имя то так, то совсем иначе, писать в карточку нечего.
    lines = [("Meet Khajiit here.", "Встреть каджита здесь."),
             ("Meet Khajiit here.", "Встреть хаджиита здесь."),
             ("Meet Khajiit here.", "Встреть кота здесь.")]
    vocab = C.build({"v": _meta(sex="m")}, {"v": lines})["v"].vocab
    assert vocab.get("khajiit", "") == ""


def test_the_official_table_outranks_our_own_usage():
    lines = [("Talk to Calcelmo about it.", "Поговори с Колсельмо об этом.")] * 4
    cards = C.build({"v": _meta(sex="f")}, {"v": lines},
                    official={"Calcelmo": "Калсельмо"})
    assert cards["v"].vocab.get("calcelmo") == "Калсельмо"


# ── блок промпта ──────────────────────────────────────────────────────────────


def test_an_empty_card_costs_nothing():
    # Неизвестный говорящий не должен добавлять в промпт ни одного токена.
    assert C.Card(voice_type="v").prompt_block() == ""


def test_the_block_names_the_speaker_and_the_race():
    card = C.build({"v": _meta(sex="m", race="NordRace")}, {"v": PLAIN})["v"]
    block = card.prompt_block()
    assert "Берг" in block and "Nord" in block
