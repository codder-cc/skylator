"""Трасса каждого вызова модели: от границы mlx_lm до строки в слое кандидатов.

Аудит 27.09 («Что нужно, чтобы следующие эксперименты были доказательными», п. 3):
эксперимент не мог доказать, что видела модель. На мастере оставались логи «карточка
приложена», на агенте — ничего. Здесь проверяется, что для каждого вызова — перевод,
одиночный повтор, кандидат, судья — агент пишет, что ФАКТИЧЕСКИ получил mlx_lm, хранит
это через перезапуск и доставляет вместе со строкой обоими путями (отправка и сверка).

Исполняется настоящий путь: пакет хоста → _persist_offline_chunk → ResultStore →
OfflineTranslateRunner → MlxBackend. Подменена только сама библиотека mlx_lm —
записывающей заглушкой, как в tests/test_runtime_contracts.py (mlx_probe).
"""
import asyncio
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest
from flask import Flask

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "remote_worker"))

import offline_translate                                              # noqa: E402
from offline_translate import OfflineTranslateRunner                  # noqa: E402
from result_store import ResultStore, SCHEMA_VERSION, compute_hash    # noqa: E402
from models.mlx_backend import MlxBackend                             # noqa: E402
from models.base import ModelState                                    # noqa: E402
from models.inference_params import InferenceParams as AgentParams    # noqa: E402
from translator.db.database import TranslationDB                      # noqa: E402
from translator.db.repo import StringRepo                             # noqa: E402
from translator.db import candidates as C                             # noqa: E402
from translator.data_manager.string_manager import StringManager      # noqa: E402
from translator.web.pull_reconcile import apply_pulled_results        # noqa: E402
from translator.web.worker_registry import WorkerRegistry, WorkerInfo  # noqa: E402


KEY = "('05000001', 'INFO', 'NAM1', 0, 0)"
EN = "I know this place and remember the old road."
RU = "Я знаю это место и помню старую дорогу."
AID = "trace-assignment"
PROMPT_TOKENS = 777          # что «сообщает» заглушка mlx_lm в каждом отклике


# ── стенд ────────────────────────────────────────────────────────────────────

def mlx_probe(monkeypatch, answers):
    """Настоящий MlxBackend до mlx_lm.stream_generate/generate; вызовы записываются."""
    calls = []
    answers = iter(answers)
    lib = ModuleType("mlx_lm")
    samples = ModuleType("mlx_lm.sample_utils")
    # sampler и processors — это то, что уходит в mlx_lm; заглушка возвращает свои
    # аргументы, поэтому сравнение трассы с вызовом — сравнение с границей библиотеки.
    samples.make_sampler = lambda **kw: dict(kw)
    samples.make_logits_processors = lambda **kw: dict(kw)

    def stream(model, tokenizer, **kw):
        calls.append(kw)
        text = next(answers, "")
        yield NS(text=text, finish_reason="stop", prompt_tokens=PROMPT_TOKENS,
                 generation_tokens=len(text.split()) or 1)

    def generate(model, tokenizer, **kw):
        calls.append(kw)
        return next(answers, "")

    lib.stream_generate = stream
    lib.generate = generate
    monkeypatch.setitem(sys.modules, "mlx_lm", lib)
    monkeypatch.setitem(sys.modules, "mlx_lm.sample_utils", samples)
    b = MlxBackend(NS(repo_id="trace-model", temperature=.3, top_p=.9,
                      repetition_penalty=1.05, max_new_tokens=100, batch_size=8))
    b._state = ModelState.LOADED
    b._model, b._tokenizer = object(), NS(encode=lambda s: s.split())
    return b, calls


def item(n=1, **changes):
    d = dict(id=n, key=KEY if n == 1 else f"('0500000{n}', 'INFO', 'NAM1', 0, 0)",
             esp="Audit.esp", mod_name="AuditMod", original=EN, rec_type="INFO",
             string_hash=compute_hash(changes.get("original", EN)))
    d.update(changes)
    return d


def seed(store, items, **meta):
    from remote_server import _persist_offline_chunk
    package = dict(offline_job_id=AID, host_job_id="trace-job", strings=items, **meta)
    package.setdefault("params", {"batch_size": 8})
    _persist_offline_chunk(NS(result_store=store), json.loads(json.dumps(package)))
    return json.loads(store.get_assignment(AID)["params_json"])


def produce(store, meta, backend, aid=AID):
    async def run():
        await OfflineTranslateRunner(store, aid, meta).run(
            NS(backend=backend), asyncio.get_running_loop())
    asyncio.run(run())
    return store.results_since(0)


def all_traces(store):
    ids = [r[0] for r in store._conn.execute("SELECT trace_id FROM agent_traces ORDER BY trace_id")]
    got = store.get_traces(ids)
    return [got[i] for i in ids]


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def payload(rows):
    from remote_server import _row_to_result
    # Через JSON, как по сети: всё, что не сериализуется, упало бы здесь.
    return json.loads(json.dumps([_row_to_result(r) for r in rows]))


@pytest.fixture
def store(tmp_path):
    s = ResultStore(tmp_path / "agent.db")
    yield s
    s.close()


def host_repo(path):
    db = TranslationDB(path)
    r = StringRepo(db)
    r.upsert("AuditMod", "Audit.esp", KEY, EN, RU, "translated", 100,
             form_id="05000001", rec_type="INFO", field_type="NAM1", source="ai",
             string_hash=compute_hash(EN))
    C.set_layer_only(r, True)
    return db, r


# ── граница mlx_lm ───────────────────────────────────────────────────────────

def test_trace_params_are_exactly_what_mlx_lm_received(store, monkeypatch):
    meta = seed(store, [item(rival="Я знаю эту дорогу.")], judge=True,
                params={"batch_size": 8, "temperature": .2, "top_k": 5,
                        "repetition_penalty": 1.1, "max_tokens": 321})
    backend, calls = mlx_probe(monkeypatch, ["1. " + RU, "B", "A"])
    rows = produce(store, meta, backend)
    traces = all_traces(store)

    assert [t["kind"] for t in traces] == ["translate", "judge", "judge"]
    assert len(calls) == len(traces) == 3
    for call, t in zip(calls, traces):
        p = t["params_json"]
        assert p["sampler"] == call["sampler"]
        assert p["logits_processors"] == call.get("logits_processors")
        assert p["max_tokens"] == call["max_tokens"]
        assert t["prompt_sha"] == sha(call["prompt"])
        assert t["prompt"] is None, "без trace_full промпт целиком не хранится"
        assert t["finish_reason"] == "stop"
        assert t["tokens_in"] == PROMPT_TOKENS
        assert t["model"] == "mlx:trace-model"
        assert t["code_rev"] == offline_translate.CODE_REV
        assert t["string_ids"] == [1]
    # Переопределения вызова реально дошли до библиотеки и видны в трассе.
    assert traces[0]["params_json"]["sampler"] == {"temp": .2, "top_p": .9, "top_k": 5}
    assert traces[0]["params_json"]["logits_processors"] == {"repetition_penalty": 1.1}
    assert traces[0]["params_json"]["max_tokens"] == 321
    assert traces[1]["params_json"]["sampler"]["top_k"] == 1
    assert traces[1]["params_json"]["sampler"]["temp"] == 0.0
    assert traces[1]["params_json"]["max_tokens"] == 8
    # Строка ссылается на вызов, давший перевод, и на оба вызова судьи.
    assert rows[0]["trace_id"] == traces[0]["trace_id"]
    assert json.loads(rows[0]["judge_trace_ids"]) == [traces[1]["trace_id"], traces[2]["trace_id"]]


def test_code_rev_is_the_agent_checkout_head():
    # В git-рабочей копии — полный HEAD, иначе пусто; ничего третьего.
    rev = offline_translate.CODE_REV
    assert rev == "" or (len(rev) == 40 and all(c in "0123456789abcdef" for c in rev))


def test_non_stream_generate_path_records_last_call(monkeypatch):
    b, calls = mlx_probe(monkeypatch, ["Ответ"])
    out = b._infer("PROMPT one two", AgentParams(top_k=3, max_tokens=50))
    assert out == "Ответ"
    info = b.last_call
    assert info["params"]["sampler"] == calls[0]["sampler"]
    assert info["params"]["max_tokens"] == calls[0]["max_tokens"] == 50
    assert info["params"]["stream"] is False
    assert info["tokens_in"] == 3            # токенизатор модели, не догадка
    assert info["finish_reason"] is None     # generate() причину не сообщает


# ── повтор и кандидаты ───────────────────────────────────────────────────────

def test_single_retry_lines_carry_their_own_retry_trace(store, monkeypatch):
    b2 = item(2, original="I remember the bridge.")
    meta = seed(store, [item(), b2])
    backend, calls = mlx_probe(monkeypatch, ["1. " + RU, "1. " + RU, "1. Я помню мост."])
    rows = produce(store, meta, backend)
    traces = {t["trace_id"]: t for t in all_traces(store)}
    assert [t["kind"] for t in traces.values()] == ["translate", "retry", "retry"]
    assert list(traces.values())[0]["string_ids"] == [1, 2]
    by_sid = {r["string_id"]: traces[r["trace_id"]] for r in rows}
    assert by_sid[1]["kind"] == by_sid[2]["kind"] == "retry"
    assert by_sid[1]["string_ids"] == [1] and by_sid[2]["string_ids"] == [2]
    assert by_sid[2]["prompt_sha"] == sha(calls[2]["prompt"])


def test_candidate_winner_points_at_the_candidate_call(store, monkeypatch):
    meta = seed(store, [item()], candidates=2)
    backend, calls = mlx_probe(monkeypatch, ["1. Первый ответ о дороге и месте.",
                                             "1. Второй ответ о дороге и месте.", "B", "A"])
    rows = produce(store, meta, backend)
    traces = {t["trace_id"]: t for t in all_traces(store)}
    assert [t["kind"] for t in traces.values()] == ["translate", "candidate", "judge", "judge"]
    won = traces[rows[0]["trace_id"]]
    assert rows[0]["translation"].startswith("Второй")
    assert won["kind"] == "candidate"
    assert won["params_json"]["sampler"] == calls[1]["sampler"]
    assert won["params_json"]["sampler"]["top_k"] == 40


# ── перезапуск, доставка, trace_full ─────────────────────────────────────────

def test_trace_survives_agent_restart_and_reaches_payload(tmp_path, monkeypatch):
    path = tmp_path / "agent.db"
    s = ResultStore(path)
    meta = seed(s, [item(rival="Я знаю эту дорогу.")], judge=True)
    backend, calls = mlx_probe(monkeypatch, ["1. " + RU, "B", "A"])
    produce(s, meta, backend)
    s.close()

    s2 = ResultStore(path)                   # перезапуск агента
    try:
        push = payload(s2.undelivered())
        pull = payload(s2.results_since(0))
    finally:
        s2.close()
    assert push == pull
    trace = push[0]["trace"]
    assert trace["kind"] == "translate"
    assert trace["prompt_sha"] == sha(calls[0]["prompt"])
    assert trace["params"]["sampler"] == calls[0]["sampler"]
    assert trace["params"]["max_tokens"] == calls[0]["max_tokens"]
    assert trace["tokens_in"] == PROMPT_TOKENS and trace["tokens_out"] >= 1
    assert trace["seconds"] is not None and trace["code_rev"] == offline_translate.CODE_REV
    assert trace["model"] == "mlx:trace-model" and trace["finish_reason"] == "stop"
    assert "prompt" not in trace
    assert [t["kind"] for t in push[0]["judge_trace"]] == ["judge", "judge"]
    assert [t["prompt_sha"] for t in push[0]["judge_trace"]] == [sha(c["prompt"]) for c in calls[1:]]
    # Ответ судьи хранится и без полной трассы: иначе «не уверен» не отличить от сбоя.
    assert [t.get("output") for t in push[0]["judge_trace"]] == ["B", "A"]


@pytest.mark.parametrize("full", [True, False])
def test_full_prompt_only_with_trace_full(store, monkeypatch, full):
    extra = {"trace_full": True} if full else {}
    meta = seed(store, [item()], **extra)
    backend, calls = mlx_probe(monkeypatch, ["1. " + RU])
    rows = produce(store, meta, backend)
    trace = payload(rows)[0]["trace"]
    stored = store._conn.execute("SELECT prompt FROM agent_traces").fetchone()[0]
    if full:
        assert trace["prompt"] == calls[0]["prompt"] == stored
    else:
        assert "prompt" not in trace and stored is None
    assert trace["prompt_sha"] == sha(calls[0]["prompt"])


def push_client(repo, tmp_path):
    from translator.web.routes.api import bp
    app = Flask(__name__)
    app.register_blueprint(bp)
    reg = WorkerRegistry()
    reg.register(WorkerInfo(label="trace-agent", url="http://unused", model="current-model"))
    reg.register_offline_job(AID, "trace-job", "trace-agent", 1)
    jm = MagicMock()
    jm.get_job.return_value = None
    app.config.update(TESTING=True, WORKER_REGISTRY=reg, JOB_MANAGER=jm, STRING_REPO=repo,
                      TRANSLATOR_CFG=NS(paths=NS(mods_dir=tmp_path)))
    return app.test_client()


def _candidate(db):
    return db.execute("SELECT * FROM candidates").fetchone()


def _assert_row_has_trace(row, sent):
    t = sent["trace"]
    assert row["prompt_sha"] == t["prompt_sha"]
    assert row["prompt"] == t["prompt"]
    assert json.loads(row["params_json"]) == t["params"]
    assert row["tokens_in"] == t["tokens_in"] and row["tokens_out"] == t["tokens_out"]
    assert row["seconds"] == pytest.approx(t["seconds"])
    assert row["code_rev"] == t["code_rev"]
    assert row["trace_kind"] == "translate"
    assert json.loads(row["judge_trace_json"]) == sent["judge_trace"]


def test_host_candidate_row_carries_trace_via_push_and_pull(store, tmp_path, monkeypatch):
    meta = seed(store, [item(rival="Я знаю эту дорогу.")], judge=True, trace_full=True)
    backend, calls = mlx_probe(monkeypatch, ["1. Я помню это место и старую дорогу.", "B", "A"])
    sent = payload(produce(store, meta, backend))
    assert sent[0]["trace"]["prompt"] == calls[0]["prompt"]

    push_db, push_repo = host_repo(tmp_path / "push.db")
    r = push_client(push_repo, tmp_path).post(
        "/api/workers/trace-agent/offline-results",
        json={"offline_job_id": AID, "results": sent, "batch_max_seq": sent[0]["seq"]})
    assert r.status_code == 200 and r.json["confirmed_seq"] == sent[0]["seq"]
    _assert_row_has_trace(_candidate(push_db), sent[0])

    pull_db, pull_repo = host_repo(tmp_path / "pull.db")
    m = StringManager(pull_repo, tmp_path)
    m._terms = {}
    apply_pulled_results(m, None, "trace-agent", sent)
    _assert_row_has_trace(_candidate(pull_db), sent[0])
    push_db.close()
    pull_db.close()


def test_old_candidates_table_gains_trace_columns_in_place(tmp_path):
    db, repo = host_repo(tmp_path / "old.db")
    db.execute("""CREATE TABLE candidates (id INTEGER PRIMARY KEY AUTOINCREMENT,
        string_id INTEGER, mod_name TEXT, esp_name TEXT, key TEXT, rec_type TEXT,
        field_type TEXT, original TEXT, translation TEXT, stored_at_arrival TEXT,
        same_as_stored INTEGER, machine TEXT, model TEXT, job_id TEXT, produced_at REAL,
        received_at REAL, score INTEGER, rules_status TEXT, issues TEXT, gate TEXT,
        UNIQUE(string_id, machine, produced_at))""")
    db.commit()
    cid = C.record(repo, string_id=1, mod_name="AuditMod", esp_name="Audit.esp", key=KEY,
                   original=EN, translation=RU, machine="m", model="x", job_id="j",
                   produced_at=1.0, terms={},
                   trace={"kind": "retry", "prompt_sha": "abc", "params": {"max_tokens": 9},
                          "tokens_in": 3, "tokens_out": 2, "seconds": .5, "code_rev": "r"})
    row = db.execute("SELECT * FROM candidates WHERE id=?", (cid,)).fetchone()
    assert row["prompt_sha"] == "abc" and row["trace_kind"] == "retry"
    assert json.loads(row["params_json"]) == {"max_tokens": 9} and row["prompt"] is None
    # Доставка старого агента (без трассы) и мусор в поле пишутся, а не отвергаются.
    assert C.record(repo, string_id=1, mod_name="AuditMod", esp_name="Audit.esp", key=KEY,
                    original=EN, translation=RU, machine="m", model="x", job_id="j",
                    produced_at=2.0, terms={}, trace="garbage", judge_trace={"x": 1})
    db.close()


# ── старая база агента ───────────────────────────────────────────────────────

_V6_SCHEMA = """
CREATE TABLE agent_meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE agent_assignments (assignment_id TEXT PRIMARY KEY, job_id TEXT NOT NULL DEFAULT '',
    mod_name TEXT, context TEXT, params_json TEXT, state TEXT NOT NULL DEFAULT 'open',
    created_at REAL);
CREATE TABLE agent_manifest (assignment_id TEXT NOT NULL, string_id INTEGER NOT NULL,
    string_hash TEXT NOT NULL, original TEXT NOT NULL, mod_name TEXT, esp_name TEXT,
    str_key TEXT, current TEXT, req_terms TEXT, rec_type TEXT, field_type TEXT, speaker TEXT,
    style TEXT, entities TEXT, talk TEXT, rival TEXT, done INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (assignment_id, string_id));
CREATE TABLE agent_results (seq INTEGER PRIMARY KEY AUTOINCREMENT, assignment_id TEXT NOT NULL,
    string_id INTEGER NOT NULL, string_hash TEXT NOT NULL, original TEXT NOT NULL,
    translation TEXT NOT NULL, quality_score INTEGER, status TEXT NOT NULL, mod_name TEXT,
    esp_name TEXT, str_key TEXT, delivered INTEGER NOT NULL DEFAULT 0, produced_at REAL,
    judge TEXT, rival TEXT, finish_reason TEXT, model TEXT);
INSERT INTO agent_meta VALUES ('schema_version', '6');
"""


def test_v6_agent_db_migrates_in_place_keeping_pending_work(tmp_path, monkeypatch):
    path = tmp_path / "v6.db"
    con = sqlite3.connect(str(path))
    con.executescript(_V6_SCHEMA)
    con.execute("INSERT INTO agent_assignments (assignment_id, job_id, params_json, state) "
                "VALUES (?, 'trace-job', ?, 'open')", (AID, json.dumps({"params": {}})))
    for sid, original in ((1, EN), (2, "I remember the bridge.")):
        con.execute("INSERT INTO agent_manifest (assignment_id, string_id, string_hash, original,"
                    " mod_name, esp_name, str_key, rec_type, done) VALUES (?,?,?,?,?,?,?,?,?)",
                    (AID, sid, compute_hash(original), original, "AuditMod", "Audit.esp",
                     KEY, "INFO", 1 if sid == 1 else 0))
    # Строка, сделанная до обновления и ещё не доставленная.
    con.execute("INSERT INTO agent_results (assignment_id, string_id, string_hash, original,"
                " translation, quality_score, status, produced_at, finish_reason, model)"
                " VALUES (?,1,?,?,?,100,'translated',1.0,'stop','old-model')",
                (AID, compute_hash(EN), EN, RU))
    con.commit()
    con.close()

    s = ResultStore(path)
    try:
        assert s.get_meta("schema_version") == str(SCHEMA_VERSION) == "9"
        assert [p["string_id"] for p in s.pending_items(AID)] == [2]
        old = payload(s.undelivered())
        assert len(old) == 1 and "trace" not in old[0] and old[0]["model"] == "old-model"
        meta = json.loads(s.get_assignment(AID)["params_json"])
        backend, calls = mlx_probe(monkeypatch, ["1. Я помню мост."])
        rows = produce(s, meta, backend)
        new = payload([r for r in rows if r["string_id"] == 2])[0]
        assert new["trace"]["prompt_sha"] == sha(calls[0]["prompt"])
        assert new["trace"]["string_ids"] == [2]
    finally:
        s.close()


# ── мастер → агент: trace_full доезжает через всё ─────────────────────────────

def test_trace_full_reaches_runner_through_dispatch_disk_and_restart(tmp_path, monkeypatch):
    from translator.web import offline_backend as O
    from translator.web.job_manager import Job
    from translator.models.inference_params import InferenceParams
    from remote_server import _persist_offline_chunk
    monkeypatch.setattr(O, "_build_terminology", lambda _: "")
    db, repo = host_repo(tmp_path / "host.db")
    pk = tmp_path / "packages"
    reg = WorkerRegistry(persist_dir=pk)
    reg.register(WorkerInfo(label="trace-agent", url="http://unused"))
    job = Job(id="trace-job", name="trace", job_type="translate_strings")
    cfg = NS(translation=NS(source_lang="English", target_lang="Russian"))
    O.dispatch_multi(job, [("AuditMod", [item(rival="Я знаю эту дорогу.")], "")],
                     InferenceParams(max_tokens=222), [("trace-agent", None)], reg,
                     MagicMock(), repo, cfg, extra={"judge": True, "trace_full": True})
    # Хост перезапустился раньше, чем агент забрал пакет: пакет едет с диска.
    chunk = WorkerRegistry(persist_dir=pk).dequeue_chunk("trace-agent", timeout=1)
    assert chunk and chunk["trace_full"] is True

    path = tmp_path / "agent.db"
    s = ResultStore(path)
    _persist_offline_chunk(NS(result_store=s), json.loads(json.dumps(chunk)))
    s.close()
    s = ResultStore(path)                    # и агент перезапустился до начала работы
    try:
        aid = chunk["offline_job_id"]
        meta = json.loads(s.get_assignment(aid)["params_json"])
        backend, calls = mlx_probe(monkeypatch, ["1. " + RU, "B", "A"])
        rows = produce(s, meta, backend, aid=aid)
        sent = payload(rows)[0]
    finally:
        s.close()
    assert sent["trace"]["prompt"] == calls[0]["prompt"]
    assert sent["trace"]["params"]["max_tokens"] == calls[0]["max_tokens"] == 222
    assert [t["prompt"] for t in sent["judge_trace"]] == [c["prompt"] for c in calls[1:]]
    db.close()


def test_raw_model_output_is_kept_only_with_full_trace(tmp_path):
    """Сырой ответ нужен, чтобы отличить ответ модели от неотделённого рассуждения."""
    import sys as _s
    from pathlib import Path as _P
    _s.path.insert(0, str(_P(__file__).parent.parent / "remote_worker"))
    from result_store import ResultStore
    st = ResultStore(tmp_path / "a.db")
    full = st.write_trace(assignment_id="a", kind="translate", string_ids=[1], prompt="P",
                          params={}, keep_prompt=True, output="<think>x</think>1. Да")
    lean = st.write_trace(assignment_id="a", kind="translate", string_ids=[1], prompt="P",
                          params={}, keep_prompt=False, output="1. Да")
    got = st.get_traces([full, lean])
    assert got[full]["output"] == "<think>x</think>1. Да"
    assert got[lean]["output"] is None
