"""Судья между переводом и воротами — и то, что до него всё терялось по дороге.

ПОТЕРЯ. Пакет для агента собирается заново, словарём с фиксированным набором ключей.
Карточка говорящего, примеры стиля, подсказки по именам и соседние реплики разговора
в этот набор не входили, поэтому до агента не доходили НИКОГДА. Хост при этом честно
писал в журнал «Speaker card attached to N strings» — он считал то, что приложил, а не
то, что отправил, и расхождения не видел никто.

СУДЬЯ. Ворота принимают ответ, только если он строго лучше по ОЦЕНКЕ, а оценка ловит
порчу — съеденную разметку, потерянные токены, эхо, обрезанную книгу — и это её работа.
На вопрос «живее ли» она ответить не может: «вино растрачивается на твой язык» и «вино
потрачено впустую» для неё одинаковы. Поэтому живой вариант проигрывал кальке и молча
отбрасывался: за сутки 73 309 доставленных ответов изменили текст ровно ноль раз.

Сочинить под ограничением модель не умеет — пять формулировок промпта не заставили её
написать «грубиянка». Выбрать из двух умеет: на семи парах, спрошенных в обоих
порядках, шесть уверенно верных и ни одной ошибки.
"""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).parent.parent
for _p in (_ROOT, _ROOT / "remote_worker"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from offline_translate import OfflineTranslateRunner          # noqa: E402
from translator.web.offline_backend import _make_remote_strings  # noqa: E402


# ── что уходит в пакет ───────────────────────────────────────────────────────

def test_everything_the_host_knows_about_a_line_reaches_the_agent():
    bucket = [{"id": 1, "key": "k", "esp": "Mod.esp", "mod_name": "Mod",
               "original": "You brute.", "rec_type": "DIAL",
               "speaker": "The player is speaking TO: Eldawyn (female)",
               "style": "The game words it so: ...",
               "entities": "The game calls it: ...",
               "talk": 'the character answers: "..."',
               "rival": "Ты грубиян."}]
    remote, _items = _make_remote_strings(bucket, "")
    got = remote[0]
    for field in ("speaker", "style", "entities", "talk", "rival"):
        assert field in got, f"поле {field} терялось по дороге к агенту"


def test_absent_fields_do_not_bloat_the_package():
    """Пакет уходит по сети целиком, и пустые ключи в нём — чистая цена."""
    remote, _ = _make_remote_strings(
        [{"id": 1, "key": "k", "esp": "M.esp", "original": "Iron Sword"}], "Mod")
    for field in ("speaker", "style", "entities", "talk", "rival", "current"):
        assert field not in remote[0]


# ── сам судья ────────────────────────────────────────────────────────────────

class _Backend:
    """Отвечает заранее заданными буквами, по одной на запрос."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.asked = []

    def _infer(self, prompt, params=None, stop_check=None):
        self.asked.append(prompt)
        return self.answers.pop(0) if self.answers else ""


def _runner():
    return OfflineTranslateRunner.__new__(OfflineTranslateRunner)


def _ask(runner, state, stored="хранимый", fresh="новый") -> str:
    """Судья должен получить ТОТ цикл, в котором его и запустили."""
    async def go():
        return await runner._judge(state, asyncio.get_running_loop(),
                                   "source", stored, fresh, {})
    return asyncio.run(go())


def _verdict(answers) -> str:
    r = _runner()
    r._stop = False
    return _ask(r, SimpleNamespace(backend=_Backend(answers)))


def test_the_fresh_answer_wins_only_when_both_orders_agree():
    # Спрошено (хранимый, новый) → B значит «новый»; затем (новый, хранимый) → A.
    assert _verdict(["B", "A"]) == "fresh"


def test_the_stored_answer_wins_when_both_orders_agree_on_it():
    assert _verdict(["A", "B"]) == "stored"


def test_a_judge_that_always_says_the_same_letter_is_caught():
    """Выбор по МЕСТУ, а не по существу: на замере так поймалась одна пара из семи.

    Несогласие двух порядков — это «не знаю», и тогда остаётся хранимый текст:
    менять его без уверенности не на что.
    """
    assert _verdict(["B", "B"]) == "unsure"
    assert _verdict(["A", "A"]) == "unsure"


def test_an_unreadable_answer_is_not_a_verdict():
    assert _verdict(["мне кажется, лучше второй", "A"]) == "unsure"


def test_the_judge_is_asked_twice_with_the_variants_swapped():
    r = _runner()
    r._stop = False
    backend = _Backend(["B", "A"])
    _ask(r, SimpleNamespace(backend=backend), "ХРАНИМЫЙ", "НОВЫЙ")
    assert len(backend.asked) == 2
    first, second = backend.asked
    assert first.index("ХРАНИМЫЙ") < first.index("НОВЫЙ")
    assert second.index("НОВЫЙ") < second.index("ХРАНИМЫЙ")


# ── и то, что терялось ЗА проводом ───────────────────────────────────────────

def test_the_manifest_keeps_the_context_the_wire_delivered(tmp_path):
    """Бегунок берёт батч ИЗ МАНИФЕСТА, а не из пакета.

    Поэтому мало довезти контекст по сети: манифест хранил только `current` и
    `req_terms`, и всё остальное терялось здесь — уже после доставки. Карточка
    говорящего, стиль, имена, разговор, соперник и даже тип записи: подсказка
    rec_type_hint всегда получала пустоту, а хост рапортовал об успехе.
    """
    from result_store import ResultStore

    st = ResultStore(str(tmp_path / "agent.db"))
    st.add_assignment("aid", items=[{
        "string_id": 1, "original": "You brute.", "esp": "M.esp", "key": "k",
        "rec_type": "DIAL", "speaker": "TO: Eldawyn (female)", "style": "st",
        "entities": "ent", "talk": 'the character answers: "..."',
        "rival": "Ты грубиян."}])
    row = st.pending_items("aid")[0]
    for field in ("rec_type", "speaker", "style", "entities", "talk", "rival"):
        assert row.get(field), f"поле {field} терялось в манифесте агента"
    assert row["esp_name"] == "M.esp" and row["str_key"] == "k"
