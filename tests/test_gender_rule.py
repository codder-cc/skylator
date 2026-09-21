"""Род говорящего — правило на воротах записи, а не разовый скрипт.

Элдавин женщина, и система это знала точно: `EldawynVoice`, флаг пола в записи NPC_,
раса HighElfRace. Её реплики всё равно звучали мужским родом, и история одной строки
объясняет почему:

    20 сен 13:47  [gender:voice]  правка рода — верно
    21 сен 00:10  [ai]            ночной слепой прогон вернул мужской
    21 сен 11:16  [duplicate]     разнос по двойникам добил

Скрипт чинил симптом, а всё, что писалось после него, ломало обратно — тот же класс,
что и с официальной таблицей: батч это снимок, и снимок не видит доставленного после.
"""
from __future__ import annotations

import pytest

from translator.characters import gender as G


def test_a_woman_does_not_speak_of_herself_as_a_man():
    got = G.enforce("I tasted my first vintage.",
                    "Я попробовал своё первое вино.", "f")
    assert got == "Я попробовала своё первое вино."


def test_a_man_is_left_alone():
    text = "Я попробовал своё первое вино."
    assert G.enforce("I tasted it.", text, "m") == text


def test_without_a_gender_nothing_is_claimed():
    # «Наугад» — не повод ставить мужской: женских персонажей в паке полно.
    text = "Я попробовал своё первое вино."
    assert G.enforce("I tasted it.", text, None) == text


def test_a_chain_of_verbs_moves_together():
    # «Я убил медведя, выпил мёд» — «я» стоит только перед первым, и правка одного
    # звена даёт фразу, где род скачет: хуже нетронутой.
    got = G.enforce("I killed the bear and drank the mead.",
                    "Я убил медведя и выпил мёд.", "f")
    assert got == "Я убила медведя и выпила мёд."


def test_the_source_outranks_the_voice_map():
    # «I'm five months past a woman grown» произносит женщина, что бы ни говорил тип
    # голоса. На этом однажды сломался верный перевод.
    text = "Я стала взрослой и убила медведя."
    assert G.enforce("I'm a woman grown now.", text, "m") == text


def test_a_mention_of_someone_else_is_not_self_description():
    # Первая версия искала любое гендерное слово и отсеяла 1 119 строк подряд, причём
    # все ложно: «my father», «your wife», «his scales» — это про других.
    got = G.enforce("My father told me to go.", "Я пошёл к нему.", "f")
    assert got == "Я пошла к нему."


def test_an_adjective_is_not_touched():
    # «Я был готов» согласуется двумя словами, и pymorphy3 по одному этого не увидит.
    got = G.enforce("I was ready.", "Я был готов.", "f")
    assert "готов" in got, "прилагательное осталось — правка его не трогает"


def test_a_word_that_only_looks_like_a_past_verb_is_left():
    for text in ("Я должен идти.", "Я рад тебя видеть.", "Я уверен в этом."):
        assert G.enforce("whatever", text, "f") == text


def test_a_line_without_first_person_costs_nothing():
    text = "Он пришёл вчера и ушёл сегодня."
    assert G.enforce("He came and left.", text, "f") == text


def test_the_write_gate_carries_the_rule():
    # Правило обязано стоять в save_string: всё, что чинит скрипт, переписывается
    # следующим же проходом.
    import inspect
    from translator.data_manager.string_manager import StringManager
    src = inspect.getsource(StringManager.save_string)
    assert "from translator.characters import gender" in src
    assert "gender_for" in src, "пол берётся по говорящему, а не угадывается"


def test_an_inverted_word_order_is_caught_too():
    """«Рассказывала ли я тебе…» — глагол стоит ПЕРЕД «я».

    Правило, ищущее его справа от местоимения, такую форму не видело, и на живом
    прогоне вышло «Рассказывала ли я … которого я встретил»: род поехал внутри одного
    предложения, то есть стало хуже, чем было.
    """
    got = G.enforce("Did I ever tell you about the Wood Elf?",
                    "Рассказывала ли я тебе о лесном эльфе, которого я встретила?", "m")
    assert got == "Рассказывал ли я тебе о лесном эльфе, которого я встретил?"


def test_an_inverted_form_already_right_is_left_alone():
    text = "Рассказывала ли я тебе о нём?"
    assert G.enforce("Did I tell you?", text, "f") == text


def test_a_pronoun_that_is_not_the_subject_is_not_touched():
    # «У меня» и «для меня» — не подлежащее, и глагол рядом не про говорящего.
    text = "Он сказал, что видел меня вчера."
    assert G.enforce("He saw me.", text, "f") == text


def test_a_subordinate_clause_about_the_same_speaker_is_carried_along():
    """«Однажды я подумала, что услышал…» — половина фразы осталась мужской.

    Замер после боевого прогона показал именно это: первое сказуемое поправлено, а за
    союзом «что» род остался прежним, и род разъехался внутри одного предложения.
    """
    got = G.enforce("One time I thought I heard them talking.",
                    "Однажды я подумал, что услышал, как они говорят.", "f")
    assert got == "Однажды я подумала, что услышала, как они говорят."


def test_a_subordinate_verb_with_its_own_subject_is_left_alone():
    # «Произошёл» — про взрыв, а не про говорящую. Здесь правка сломала бы фразу.
    got = G.enforce("I saw that an explosion happened.",
                    "Я видел, что произошёл взрыв.", "f")
    assert got == "Я видела, что произошёл взрыв."


def test_someone_elses_subject_after_the_conjunction_blocks_nothing_of_ours():
    # «что он услышал» — подлежащее названо, и это не говорящая.
    got = G.enforce("I thought that he heard them.",
                    "Я подумал, что он услышал их.", "f")
    assert got == "Я подумала, что он услышал их."


def test_anything_may_stand_between_the_pronoun_and_the_verb():
    """Список служебных слов был закрытым, и жизнь в него не помещалась.

    Замер по корпусу после первого боевого прогона: 4,1% реплик с известным полом всё
    ещё расходились, и все три образца — про это. «Я» в русском всегда подлежащее,
    поэтому ищется ближайший глагол, а не заранее перечисленная конструкция.
    """
    assert G.enforce("I fought him.", "с которым я когда-либо сражался", "f") \
        == "с которым я когда-либо сражалась"
    assert G.enforce("Whom I said it to.", "кому я их сказала", "m") \
        == "кому я их сказал"
    assert G.enforce("I figured it out.", "я это разобрал", "f") == "я это разобрала"


def test_the_rule_does_not_cross_a_sentence_boundary():
    # «Я думаю, пришёл Марк» — за запятой чужое подлежащее, и правка сломала бы фразу.
    text = "Я думаю, пришёл Марк."
    assert G.enforce("I think Mark arrived.", text, "f") == text


def test_another_subject_in_between_stops_the_search():
    text = "Я знаю, что он сказал."
    assert G.enforce("I know what he said.", text, "f") == text


def test_a_noun_that_can_also_be_read_as_a_verb_is_left_alone():
    """«Я чувствую запах крови» → «Я чувствую запахла крови».

    Это не выдумка: скан по словам записал такое в 1 123 строки, пока разбор брался
    любой, а не самый вероятный. «Запах» — существительное, и глаголом «запахнуть» оно
    становится только во вторую очередь.
    """
    text = "Я чувствую запах крови. Много крови."
    assert G.enforce("I smell blood.", text, "f") == text


def test_a_chain_is_carried_even_when_the_first_verb_was_already_right():
    """Цепочка привязана к сказуемому, а не к факту правки.

    Пока она запускалась только после состоявшейся правки, фраза с УЖЕ верным первым
    глаголом оставалась разъехавшейся — и пережила боевой прогон именно в таком виде.
    """
    got = G.enforce("One time I thought I heard them talking.",
                    "Однажды я подумала, что услышал, как они говорят.", "f")
    assert got == "Однажды я подумала, что услышала, как они говорят."


def test_a_chain_does_not_reach_into_the_next_sentence():
    text = "Я пришла домой. Потом отец вернулся и лёг спать."
    assert G.enforce("I came home.", text, "f") == text


def test_four_words_may_stand_between_the_pronoun_and_the_verb():
    got = G.enforce("I never knew him.", "Я никогда его не знал.", "f")
    assert got == "Я никогда его не знала."


def test_a_chain_does_not_leave_a_relative_clause():
    """«Единственный человек, которого я целовала, умерла от холода».

    «Я» сидит внутри придаточного, и глагол за его границей — про человека, а не про
    говорящую. Правило записало здесь «умерла» на боевом прогоне.
    """
    text = "Единственный человек, которого я целовала, умер от холода."
    assert G.enforce("The only person I kissed died of cold.", text, "f") == text


def test_a_relative_that_after_an_antecedent_is_not_a_chain():
    """«…когда я надеялся на ту, что светилась» — «что» относится к безделушке.

    Правило записало здесь «светился» на боевом прогоне: цепочка перешагнула через
    «на ту» и присвоила говорящему чужое сказуемое.
    """
    text = ("Подарить мне такую безжизненную безделушку, когда я надеялся на ту, "
            "что светилась красным.")
    assert G.enforce("I had hoped for one that glowed red.", text, "m") == text


def test_the_speakers_own_clause_after_a_conjunction_still_moves():
    got = G.enforce("For three nights I continued, and when I was done, I knew.",
                    "Три ночи я продолжал, и когда закончила, то поняла.", "m")
    assert got == "Три ночи я продолжал, и когда закончил, то понял."
