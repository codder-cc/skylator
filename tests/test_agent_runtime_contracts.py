"""
Контракты агента, найденные аудитом сквозного выполнения (audit/runtime_validation).

Всё здесь ИСПОЛНЯЕТ рабочий код — ResultStore, _persist_offline_chunk, бегунок,
сборку промпта, MlxBackend до границы mlx_lm — и смотрит, что дошло до модели и что
легло в базу. Прежние тесты этих мест проверяли наличие строк в исходнике, и каждая из
поломок ниже проходила их зелёной:

  * MLX `_infer` выбрасывал top_k и repetition_penalty;
  * судья читал «Both translations…» как ответ B;
  * назначение с недоделанными строками помечалось complete и больше не возобновлялось;
  * причина конца генерации доживала только от последнего вызова и не доставлялась;
  * модель-производитель не записывалась;
  * field_type терялся в манифесте;
  * смешанный батч получал стиль первой строки;
  * пятый блок имён и седьмая реплика разговора молча выбрасывались;
  * одиночный повтор видел свой разговор под чужим номером и разговор соседа;
  * слепой перевод видел своего соперника через память переводов.
"""
import asyncio
import json
import sqlite3
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace as NS

import pytest

_RW = Path(__file__).resolve().parents[1] / "remote_worker"
if str(_RW) not in sys.path:
    sys.path.insert(0, str(_RW))

from result_store import ResultStore, SCHEMA_VERSION            # noqa: E402
from offline_translate import OfflineTranslateRunner, parse_judge_answer  # noqa: E402
from models.base import ModelState                               # noqa: E402
from models.inference_params import InferenceParams              # noqa: E402

RU = "Я знаю это место и помню старую дорогу."
EN = "I know this place and remember the old road."


# ── helpers ──────────────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path):
    s = ResultStore(tmp_path / "agent.db")
    yield s
    s.close()


def _item(n, **kw):
    d = dict(id=n, key=f"k{n}", esp="M.esp", mod_name="M",
             original=f"The traveller remembers village number {n}.", rec_type="INFO")
    d.update(kw)
    return d


def _seed(store, items, **meta):
    """Положить пакет так, как его кладёт агент, и прочесть метаданные как после рестарта."""
    from remote_server import _persist_offline_chunk
    pkg = dict(offline_job_id="aid", host_job_id="job", strings=items,
               params={"batch_size": 8}, **meta)
    _persist_offline_chunk(NS(result_store=store), json.loads(json.dumps(pkg)))
    return json.loads(store.get_assignment("aid")["params_json"])


class _Backend:
    """Отвечает по списку. (текст, причина) задаёт last_finish_reason ЭТОГО вызова."""

    def __init__(self, answers=(), default=""):
        self.answers = list(answers)
        self.default = default
        self.prompts = []
        self.last_finish_reason = "stop"

    def _infer(self, prompt, params=None, stop_check=None):
        self.prompts.append(prompt)
        a = self.answers.pop(0) if self.answers else self.default
        if isinstance(a, tuple):
            a, self.last_finish_reason = a
        else:
            self.last_finish_reason = "stop"
        return a


class _EchoBackend(_Backend):
    """Отвечает на ЛЮБОЙ батч полным нумерованным списком нужной длины."""

    def _infer(self, prompt, params=None, stop_check=None):
        self.prompts.append(prompt)
        self.last_finish_reason = "stop"
        body = prompt.split("Strings:")[-1]
        n = sum(1 for ln in body.splitlines() if ln[:1].isdigit() and ". " in ln)
        return "\n".join(f"{k}. {RU}" for k in range(1, n + 1))


def _produce(store, meta, backend, **state):
    async def go():
        await OfflineTranslateRunner(store, "aid", meta).run(
            NS(backend=backend, **state), asyncio.get_running_loop())
    asyncio.run(go())
    return store.results_since(0)


def _prompt_for(prompts, sentinel):
    got = [p for p in prompts if sentinel in p]
    assert got, f"{sentinel} не дошёл ни до одного промпта"
    return got[0]


# ── MLX: параметры сэмплинга доходят до mlx_lm на всех путях ──────────────────

def _mlx(monkeypatch, *, sampler_takes_top_k=True, with_processors=True):
    from models.mlx_backend import MlxBackend
    calls = []
    lib = ModuleType("mlx_lm")
    su = ModuleType("mlx_lm.sample_utils")
    if sampler_takes_top_k:
        su.make_sampler = lambda **kw: dict(kw)
    else:
        def make_sampler(temp=0.0, top_p=0.0):
            return {"temp": temp, "top_p": top_p}
        su.make_sampler = make_sampler
    if with_processors:
        su.make_logits_processors = lambda **kw: dict(kw)

    def stream(model, tok, **kw):
        calls.append(kw)
        yield NS(text="1. да", finish_reason="stop")

    def generate(model, tok, **kw):
        calls.append(kw)
        return "1. да"

    lib.stream_generate, lib.generate = stream, generate
    monkeypatch.setitem(sys.modules, "mlx_lm", lib)
    monkeypatch.setitem(sys.modules, "mlx_lm.sample_utils", su)
    cfg = NS(repo_id="t", temperature=.3, top_p=.9, top_k=20, repetition_penalty=1.05,
             max_new_tokens=100, batch_size=8, source_lang="English", target_lang="Russian")
    b = MlxBackend(cfg)
    b._state = ModelState.LOADED
    b._model = object()
    b._tokenizer = NS(apply_chat_template=lambda *a, **k: "PROMPT")
    return b, calls


@pytest.mark.parametrize("stop_check", [lambda: False, None], ids=["stream", "generate"])
def test_mlx_infer_passes_top_k_and_repetition_penalty(monkeypatch, stop_check):
    b, calls = _mlx(monkeypatch)
    b._infer("P", InferenceParams(top_k=7, repetition_penalty=1.17), stop_check=stop_check)
    assert calls[0]["sampler"]["top_k"] == 7
    assert calls[0]["logits_processors"]["repetition_penalty"] == 1.17


def test_mlx_infer_falls_back_to_config_values(monkeypatch):
    b, calls = _mlx(monkeypatch)
    b._infer("P", InferenceParams(), stop_check=lambda: False)
    assert calls[0]["sampler"]["top_k"] == 20
    assert calls[0]["logits_processors"]["repetition_penalty"] == 1.05


def test_mlx_translate_and_chat_share_the_same_sampling(monkeypatch):
    b, calls = _mlx(monkeypatch)
    b.translate(["Hello"], params=InferenceParams(top_k=3, repetition_penalty=1.2))
    b._chat("hi", temperature=0.5)
    assert calls[0]["sampler"]["top_k"] == 3
    assert calls[0]["logits_processors"]["repetition_penalty"] == 1.2
    assert calls[1]["sampler"]["temp"] == 0.5 and calls[1]["sampler"]["top_k"] == 20


def test_mlx_judge_top_k_one_reaches_the_sampler(monkeypatch):
    """top_k=1 судьи — это и есть его детерминированность."""
    b, calls = _mlx(monkeypatch)
    r = OfflineTranslateRunner.__new__(OfflineTranslateRunner)
    r._stop = False

    async def go():
        return await r._judge(NS(backend=b), asyncio.get_running_loop(),
                              "src", "a", "b", InferenceParams())
    asyncio.run(go())
    assert calls and all(c["sampler"]["top_k"] == 1 for c in calls)


def test_mlx_old_library_without_top_k_still_generates(monkeypatch):
    b, calls = _mlx(monkeypatch, sampler_takes_top_k=False, with_processors=False)
    assert b._infer("P", InferenceParams(top_k=7, repetition_penalty=1.2),
                    stop_check=lambda: False) == "1. да"
    assert "top_k" not in calls[0]["sampler"]
    assert "logits_processors" not in calls[0]


# ── Судья: ответ — ровно буква ───────────────────────────────────────────────

@pytest.mark.parametrize("raw,letter", [
    ("A", "A"), (" b ", "B"), ("A.", "A"), ("B!", "B"),
    ("Both translations are equally good.", "?"), ("A or B", "?"), ("garbage", "?"),
    ("", "?"), ("Answer: A", "?"),
])
def test_judge_answer_parsing_is_strict(raw, letter):
    assert parse_judge_answer(raw) == letter


def _judge(answers):
    r = OfflineTranslateRunner.__new__(OfflineTranslateRunner)
    r._stop = False
    b = _Backend(answers)

    async def go():
        loop = asyncio.get_running_loop()
        return (await r._judge(NS(backend=b), loop, "s", "old", "new", InferenceParams()),
                await r._judge_detail(NS(backend=_Backend(answers)), loop, "s", "old", "new",
                                      InferenceParams()))
    return asyncio.run(go())


def test_malformed_judge_output_is_unsure_for_the_decision_but_invalid_on_record():
    assert _judge(["Both translations are equally good.", "A"]) == ("unsure", "invalid")
    assert _judge(["B", "A"]) == ("fresh", "fresh")
    assert _judge(["A", "B"]) == ("stored", "stored")
    assert _judge(["A", "A"]) == ("unsure", "unsure")


def test_runner_stores_invalid_verdict_distinguishably(store):
    meta = _seed(store, [_item(1, rival="Старый перевод строки.")], judge=True)
    rows = _produce(store, meta, _Backend(["1. " + RU, "Both are fine.", "A"]))
    assert rows[0]["judge"] == "invalid"
    assert rows[0]["translation"] == RU


# ── Назначение с остатком не закрывается ─────────────────────────────────────

def _produce_assignment(store, backend):
    from remote_server import _produce_assignment

    async def go():
        await _produce_assignment(NS(result_store=store, backend=backend),
                                  asyncio.get_running_loop(), "aid",
                                  json.loads(store.get_assignment("aid")["params_json"]))
    asyncio.run(go())
    return store.get_assignment("aid")["state"]


def test_assignment_with_pending_rows_stays_open(store):
    _seed(store, [_item(1), _item(2)])
    assert _produce_assignment(store, _Backend(default="")) == "open"
    assert len(store.pending_items("aid")) == 2
    # И следующий заход его доделывает, после чего оно закрывается.
    assert _produce_assignment(store, _EchoBackend()) == "complete"
    assert store.pending_items("aid") == []


# ── Схема: миграция старой базы и свежая база ────────────────────────────────

_V5_SCHEMA = """
CREATE TABLE agent_meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE agent_assignments (assignment_id TEXT PRIMARY KEY, job_id TEXT NOT NULL DEFAULT '',
    mod_name TEXT, context TEXT, params_json TEXT, state TEXT NOT NULL DEFAULT 'open',
    created_at REAL);
CREATE TABLE agent_manifest (assignment_id TEXT NOT NULL, string_id INTEGER NOT NULL,
    string_hash TEXT NOT NULL, original TEXT NOT NULL, mod_name TEXT, esp_name TEXT,
    str_key TEXT, current TEXT, req_terms TEXT, rec_type TEXT, speaker TEXT, style TEXT,
    entities TEXT, talk TEXT, rival TEXT, done INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (assignment_id, string_id));
CREATE TABLE agent_results (seq INTEGER PRIMARY KEY AUTOINCREMENT, assignment_id TEXT NOT NULL,
    string_id INTEGER NOT NULL, string_hash TEXT NOT NULL, original TEXT NOT NULL,
    translation TEXT NOT NULL, quality_score INTEGER, status TEXT NOT NULL, mod_name TEXT,
    esp_name TEXT, str_key TEXT, delivered INTEGER NOT NULL DEFAULT 0, produced_at REAL,
    judge TEXT, rival TEXT);
INSERT INTO agent_meta VALUES ('schema_version', '5');
INSERT INTO agent_assignments (assignment_id, state) VALUES ('old', 'open');
INSERT INTO agent_manifest (assignment_id, string_id, string_hash, original)
    VALUES ('old', 1, 'h', 'Old pending line.');
"""


def _columns(path, table):
    con = sqlite3.connect(str(path))
    try:
        return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    finally:
        con.close()


def test_previous_schema_db_is_migrated_in_place(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(str(path))
    con.executescript(_V5_SCHEMA)
    con.close()
    s = ResultStore(path)
    try:
        assert s.get_meta("schema_version") == str(SCHEMA_VERSION) == "7"
        assert s.pending_items("old")[0]["original"] == "Old pending line."
        seq = s.write_result("old", 1, "Old pending line.", "Старая строка.", 100,
                             "translated", finish_reason="length", model="m-1")
        row = s.results_since(0)[0]
        assert row["seq"] == seq and row["finish_reason"] == "length" and row["model"] == "m-1"
    finally:
        s.close()
    assert "field_type" in _columns(path, "agent_manifest")


def test_fresh_db_has_the_new_columns_without_migrations(tmp_path):
    s = ResultStore(tmp_path / "new.db")
    s.close()
    assert {"field_type"} <= _columns(tmp_path / "new.db", "agent_manifest")
    assert {"finish_reason", "model"} <= _columns(tmp_path / "new.db", "agent_results")


# ── Причина конца генерации и модель доезжают до мастера ─────────────────────

def test_delivery_payload_carries_finish_reason_and_producer_model(store):
    from remote_server import _row_to_result
    meta = _seed(store, [_item(1)])
    rows = _produce(store, meta, _Backend([("1. " + RU, "length")]),
                    model_label="producer-model")
    wire = _row_to_result(rows[0])
    assert wire["finish_reason"] == "length"
    assert wire["model"] == "producer-model"
    assert wire["status"] == "needs_review"


def test_old_rows_without_the_columns_deliver_empty_values():
    from remote_server import _row_to_result
    wire = _row_to_result(dict(seq=1, assignment_id="a", string_id=1, string_hash="h",
                               original="x", translation="y", status="translated",
                               quality_score=100, produced_at=1.0))
    assert wire["finish_reason"] == "" and wire["model"] == ""


# ── field_type: переживает манифест и уточняет подсказку ──────────────────────

def test_field_type_survives_manifest_and_refines_the_hint(store):
    meta = _seed(store, [_item(1, rec_type="BOOK", field_type="DESC",
                               original="The book describes a distant village.")])
    assert store.pending_items("aid")[0]["field_type"] == "DESC"
    b = _Backend(["1. Книга описывает далёкую деревню."])
    _produce(store, meta, b)
    assert "These strings are book text." in b.prompts[0]


def test_package_without_field_type_still_works(store):
    meta = _seed(store, [_item(1, rec_type="BOOK", original="A long book text here.")])
    assert store.pending_items("aid")[0]["field_type"] is None
    b = _Backend(["1. Длинный текст книги."])
    _produce(store, meta, b)
    assert "These strings are book titles or text." in b.prompts[0]


# ── Однородные батчи ─────────────────────────────────────────────────────────

def test_interleaved_record_types_each_get_their_own_style(store):
    items = []
    for n in range(1, 7):
        kind = "INFO" if n % 2 else "BOOK"
        items.append(_item(n, rec_type=kind, style=f"{kind}_STYLE_SENTINEL"))
    meta = _seed(store, items)
    b = _EchoBackend()
    rows = _produce(store, meta, b)
    assert len(rows) == 6
    assert len(b.prompts) == 2
    for p in b.prompts:
        assert ("INFO_STYLE_SENTINEL" in p) != ("BOOK_STYLE_SENTINEL" in p)


# ── Контекст каждой строки: не выбрасывается, а разбивает батч ───────────────

def test_every_entities_block_reaches_generation(store):
    items = [_item(n, entities=f"ENTITY_{n}_SENTINEL") for n in range(1, 10)]
    meta = _seed(store, items)
    b = _EchoBackend()
    rows = _produce(store, meta, b)
    assert len(rows) == 9
    for n in range(1, 10):
        p = _prompt_for(b.prompts, f"ENTITY_{n}_SENTINEL")
        assert f"village number {n}." in p, "имена строки пришли без самой строки"
    assert len(b.prompts) >= 2, "девять блоков имён не помещаются в один промпт"


def test_every_conversation_line_reaches_generation(store):
    items = [_item(n, talk="\n".join(f"TALK_{n}_{k}" for k in range(5)))
             for n in range(1, 6)]
    meta = _seed(store, items)
    b = _EchoBackend()
    _produce(store, meta, b)
    for n in range(1, 6):
        for k in range(5):
            _prompt_for(b.prompts, f"TALK_{n}_{k}")


def test_single_line_keeps_all_its_context_however_long(store):
    items = [_item(1, talk="\n".join(f"LONG_TALK_{k}" for k in range(30)))]
    meta = _seed(store, items)
    b = _EchoBackend()
    _produce(store, meta, b)
    assert all(f"LONG_TALK_{k}" in b.prompts[0] for k in range(30))


# ── Одиночный повтор: контекст своей строки под номером 1 ────────────────────

def test_single_retry_rebuilds_context_for_that_line(store):
    items = [_item(1, talk="FIRST_TALK", entities="FIRST_NAMES"),
             _item(2, talk="SECOND_TALK", entities="SECOND_NAMES")]
    meta = _seed(store, items)
    b = _Backend(["1. " + RU, "1. " + RU, "1. Путник помнит вторую деревню."])
    rows = _produce(store, meta, b)
    assert len(b.prompts) == 3 and len(rows) == 2
    first, second = b.prompts[1], b.prompts[2]
    assert "(1) FIRST_TALK" in first and "SECOND" not in first
    assert "(1) SECOND_TALK" in second and "SECOND_NAMES" in second
    assert "FIRST" not in second


# ── Слепой перевод не видит соперника через память переводов ─────────────────

def test_blind_line_does_not_see_its_rival_through_tm(store):
    items = [_item(1, original="Whiterun", rival="RIVAL_SENTINEL"),
             _item(2, original="Riften Whiterun road")]
    meta = _seed(store, items, judge=True,
                 tm_pairs={"Whiterun": "RIVAL_SENTINEL", "Riften": "Рифтен"})
    b = _Backend(["1. Вайтран\n2. Дорога Рифтен Вайтран", "B", "A"])
    _produce(store, meta, b)
    assert "RIVAL_SENTINEL" not in b.prompts[0]
    assert "Riften → Рифтен" in b.prompts[0], "прочая память переводов остаётся"


def test_tm_is_untouched_without_a_rival(store):
    meta = _seed(store, [_item(1, original="Whiterun")], tm_pairs={"Whiterun": "Вайтран"})
    b = _Backend(["1. Вайтран"])
    _produce(store, meta, b)
    assert "Whiterun → Вайтран" in b.prompts[0]


def test_reasoning_block_is_not_parsed_as_translation():
    """С режимом размышления нумерованные строки рассуждения не становятся переводом."""
    from prompt.parser import parse_numbered_output
    raw = "<think>\n1. First I translate the name.\n2. Then the verb.\n</think>\n\n1. Привет.\n2. Пока."
    assert parse_numbered_output(raw, 2) == ["Привет.", "Пока."]
    assert parse_numbered_output("1. Привет.\n2. Пока.", 2) == ["Привет.", "Пока."]
