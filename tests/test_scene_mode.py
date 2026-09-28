"""Режим сцены: разговор (тема DIAL + ответы по порядку) — один нумерованный батч.

Проверяется поведением, по настоящему пути: хост → dispatch_multi → JSON пакета →
_persist_offline_chunk → перезапущенный ResultStore → OfflineTranslateRunner →
MlxBackend._infer → заглушка mlx_lm. Исходники не читаются.
"""
import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest
from flask import Flask

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "remote_worker"))

from result_store import ResultStore, compute_hash
from offline_translate import OfflineTranslateRunner
from remote_server import _persist_offline_chunk
from models.mlx_backend import MlxBackend
from models.base import ModelState
from translator.db.database import TranslationDB
from translator.db.repo import StringRepo
from translator.models.inference_params import InferenceParams
from translator.web.offline_backend import dispatch_multi
from translator.web.worker_registry import WorkerRegistry, WorkerInfo
from translator.web.job_manager import Job

_spec = importlib.util.spec_from_file_location("scene_scenario", ROOT / "tests" / "scene_scenario.py")
scenario = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scenario)

GOLDEN = ROOT / "tests" / "data" / "scene_off_golden.json"

TOPIC = "audit.esp:000001"
NOTE = "These lines belong to one dialogue topic"
TALK = "Conversation around these lines"

CARD_TO = ("The player is speaking TO: Eldawyn (female), High Elf\n"
           "This line is addressed to that character, so second-person forms take their "
           "gender (feminine).")
CARD_ELDAWYN = ("Speaker: Eldawyn (female), High Elf\n"
                "Use the speaker's gender for first-person past-tense forms (feminine).")
CARD_GUARD = ("Speaker: Whiterun Guard (male), Nord\n"
              "Use the speaker's gender for first-person past-tense forms (masculine).")

LINES = {  # scene_pos → (form_id, rec, field, english)
    0: ("05000001", "DIAL", "FULL", "What do you know about the old road?"),
    1: ("05000002", "INFO", "NAM1", "I know this place and remember the old road."),
    2: ("05000003", "INFO", "NAM1", "It leads to the barrow, past the river."),
}


def run_scenario(tmp_path, extra=None):
    return scenario.run(ResultStore, OfflineTranslateRunner, _persist_offline_chunk,
                        tmp_path / "agent.db", extra)


# ── без флага — байт в байт как до режима сцены ───────────────────────────────

def test_without_scene_flag_prompts_are_byte_identical_to_before(tmp_path):
    """Золотой файл снят с кода ДО режима сцены на тех же строках (с полями scene)."""
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert run_scenario(tmp_path) == golden


def test_scene_flag_changes_only_scene_batches(tmp_path):
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    prompts = run_scenario(tmp_path, {"scene": True})
    scene = [p for p in prompts if NOTE in p]
    assert len(scene) == 1
    # Строки вне сцены — и одиночная строка своей сцены — идут прежним путём, теми же
    # промптами, что и без флага.
    rest = [p for p in prompts if NOTE not in p]
    assert rest and all(p in golden for p in rest)
    assert len(rest) == len(prompts) - 1
    lone = [p for p in rest if "You brute." in p]
    assert lone, "single-line scene must fall back to the ordinary batch"


# ── промпт сцены ──────────────────────────────────────────────────────────────

def scene_items(cards=(CARD_TO, CARD_ELDAWYN, CARD_ELDAWYN), ids=(30, 10, 20)):
    """Три строки сцены — в манифесте НЕ в порядке проигрывания (id задают порядок)."""
    out = []
    for pos, sid in zip(range(3), ids):
        fid, rec, field, en = LINES[pos]
        d = dict(id=sid, key=f"('{fid}', '{rec}', '{field}', 0, 0)", esp="Audit.esp",
                 mod_name="AuditMod", original=en, rec_type=rec, field_type=field,
                 talk=f"TALK_SENTINEL_{pos}", scene=TOPIC, scene_pos=pos)
        if cards[pos]:
            d["speaker"] = cards[pos]
        out.append(d)
    return out


class Backend:
    def __init__(self, answers):
        self.answers = iter(answers)
        self.calls = []
        self.last_finish_reason = "stop"

    def _infer(self, prompt, params=None, stop_check=None):
        self.calls.append(prompt)
        return next(self.answers, "")


def seed(store, items, **meta):
    package = dict(offline_job_id="scene-a", host_job_id="scene-job", strings=items,
                   params={"batch_size": 8})
    package.update(meta)
    _persist_offline_chunk(NS(result_store=store), json.loads(json.dumps(package)))
    return json.loads(store.get_assignment("scene-a")["params_json"])


def produce(store, meta, backend):
    async def run():
        await OfflineTranslateRunner(store, "scene-a", meta).run(
            NS(backend=backend), asyncio.get_running_loop())
    asyncio.run(run())
    return store.results_since(0)


RU3 = "1. Что ты знаешь о старой дороге?\n2. Я знаю это место.\n3. Она ведёт к кургану."


def test_scene_batch_has_all_lines_in_order_numbered_cards_no_talk(tmp_path):
    store = ResultStore(tmp_path / "a.db")
    meta = seed(store, scene_items(), scene=True)
    b = Backend([RU3])
    rows = produce(store, meta, b)
    assert len(b.calls) == 1
    p = b.calls[0]
    strings = p.split("Strings:\n", 1)[1].split("<|im_end|>", 1)[0]
    assert strings == "\n".join(f"{n + 1}. {LINES[n][3]}" for n in range(3))
    assert NOTE in p and "Line 1 is what the player says" in p
    assert TALK not in p and "TALK_SENTINEL" not in p
    assert "  (1) The player is speaking TO: Eldawyn" in p
    assert "  (2, 3) Speaker: Eldawyn" in p
    assert "These strings are" not in p     # общая подсказка по типу записи в сцене неверна
    # Ответы — по строке, по номеру: тема получила ответ №1, хотя в манифесте она последняя.
    by_id = {r["string_id"]: r["translation"] for r in rows}
    assert by_id == {30: "Что ты знаешь о старой дороге?", 10: "Я знаю это место.",
                     20: "Она ведёт к кургану."}
    store.close()


def test_mixed_speaker_scene_keeps_each_card_on_its_own_number(tmp_path):
    store = ResultStore(tmp_path / "a.db")
    meta = seed(store, scene_items(cards=(CARD_TO, CARD_ELDAWYN, CARD_GUARD)), scene=True)
    b = Backend([RU3])
    produce(store, meta, b)
    assert len(b.calls) == 1, "a scene is not split by speaker"
    ctx = b.calls[0].split("Who speaks each line:\n", 1)[1].split("\n\nStrings:", 1)[0]
    lines = ctx.splitlines()
    assert lines[0] == "  (1) The player is speaking TO: Eldawyn (female), High Elf"
    two = lines.index("  (2) Speaker: Eldawyn (female), High Elf")
    three = lines.index("  (3) Speaker: Whiterun Guard (male), Nord")
    assert 0 < two < three
    # Продолжение карточки — под своим номером, с отступом, а не отдельной строкой батча.
    assert lines[two + 1].strip() == ("Use the speaker's gender for first-person past-tense "
                                      "forms (feminine).")
    assert lines[three + 1].strip().endswith("(masculine).")
    # Ни одной карточки на весь батч: в начале контекста стоит заметка о сцене.
    assert b.calls[0].split("Context: ", 1)[1].startswith(NOTE)
    store.close()


def test_without_flag_same_items_batch_by_speaker_with_talk(tmp_path):
    store = ResultStore(tmp_path / "a.db")
    meta = seed(store, scene_items())
    b = Backend(["1. Я знаю это место.\n2. Она ведёт к кургану.", "1. Что ты знаешь?"])
    produce(store, meta, b)
    assert len(b.calls) == 2
    assert all(NOTE not in p for p in b.calls)
    assert any(TALK in p for p in b.calls)


def test_long_scene_is_split_into_consecutive_windows(tmp_path):
    items = []
    for pos in range(12):
        items.append(dict(id=100 + pos, key=f"k{pos}", esp="Audit.esp", mod_name="AuditMod",
                          original=f"Line number {pos} of the long talk.", rec_type="INFO",
                          field_type="NAM1", scene=TOPIC, scene_pos=pos))
    store = ResultStore(tmp_path / "a.db")
    meta = seed(store, list(reversed(items)), scene=True, params={"batch_size": 32})
    b = Backend(["\n".join(f"{k}. Строка {k}." for k in range(1, 11)), "1. А.\n2. Б."])
    rows = produce(store, meta, b)
    assert len(b.calls) == 2
    first = b.calls[0].split("Strings:\n", 1)[1]
    assert first.startswith("1. Line number 0 ") and "10. Line number 9 " in first
    assert "11." not in first
    second = b.calls[1].split("Strings:\n", 1)[1]
    assert second.startswith("1. Line number 10 ") and "2. Line number 11 " in second
    assert NOTE in b.calls[1]
    # Окно, которое начинается с ответа, не называет строку 1 репликой игрока.
    assert all("Line 1 is what the player says" not in c for c in b.calls)
    assert len(rows) == 12
    store.close()


# ── хост → пакет → перезапуск агента → граница mlx_lm ─────────────────────────

def mlx_probe(monkeypatch, answers):
    calls = []
    answers = iter(answers)
    lib = ModuleType("mlx_lm")
    samples = ModuleType("mlx_lm.sample_utils")
    samples.make_sampler = lambda **kw: kw
    samples.make_logits_processors = lambda **kw: kw

    def stream(model, tokenizer, **kw):
        calls.append(kw)
        yield NS(text=next(answers, ""), finish_reason="stop")

    lib.stream_generate = stream
    lib.generate = lambda model, tokenizer, **kw: (calls.append(kw), next(answers, ""))[1]
    monkeypatch.setitem(sys.modules, "mlx_lm", lib)
    monkeypatch.setitem(sys.modules, "mlx_lm.sample_utils", samples)
    b = MlxBackend(NS(repo_id="audit", temperature=.3, top_p=.9, repetition_penalty=1.05,
                      max_new_tokens=100, batch_size=8))
    b._state = ModelState.LOADED
    b._model, b._tokenizer = object(), object()
    return b, calls


def test_scene_meta_survives_dispatch_persist_restart_to_mlx(tmp_path, monkeypatch):
    from translator.web import offline_backend as O
    monkeypatch.setattr(O, "_build_terminology", lambda _: "")
    db = TranslationDB(tmp_path / "host.db")
    repo = StringRepo(db)
    registry = WorkerRegistry()
    registry.register(WorkerInfo(label="m1", url="http://unused"))
    registry.register(WorkerInfo(label="m2", url="http://unused"))
    captured = []
    registry.enqueue_chunk = lambda label, chunk: captured.append(chunk)
    job = Job(id="scene-job", name="scene", job_type="translate_strings")
    cfg = NS(translation=NS(source_lang="English", target_lang="Russian"))
    other = [dict(id=500 + k, key=f"w{k}", esp="Audit.esp", mod_name="AuditMod",
                  original=f"Weapon name {k}", rec_type="WEAP", field_type="FULL")
             for k in range(6)]
    dispatch_multi(job, [("AuditMod", other[:3] + scene_items() + other[3:], "")],
                   InferenceParams(), [("m1", None), ("m2", None)], registry, MagicMock(),
                   repo, cfg, extra={"scene": True})
    # Сцена не делится между машинами: все три строки — в одном пакете.
    holders = [c for c in captured if any(s.get("scene") for s in c["strings"])]
    assert len(holders) == 1 and holders[0]["scene"] is True
    chunk = holders[0]
    assert sorted((s["scene_pos"], s["id"]) for s in chunk["strings"] if s.get("scene")) \
        == [(0, 30), (1, 10), (2, 20)]

    store = ResultStore(tmp_path / "agent.db")
    _persist_offline_chunk(NS(result_store=store), json.loads(json.dumps(chunk)))
    store.close()
    store = ResultStore(tmp_path / "agent.db")        # перезапуск агента
    aid = chunk["offline_job_id"]
    meta = json.loads(store.get_assignment(aid)["params_json"])
    assert meta["scene"] is True
    kept = {p["string_id"]: (p["scene"], p["scene_pos"]) for p in store.pending_items(aid)
            if p.get("scene")}
    assert kept == {30: (TOPIC, 0), 10: (TOPIC, 1), 20: (TOPIC, 2)}

    n_other = sum(1 for s in chunk["strings"] if not s.get("scene"))
    other_answer = "\n".join(f"{k}. Оружие." for k in range(1, n_other + 1))
    backend, calls = mlx_probe(monkeypatch, [RU3, other_answer])

    async def run():
        await OfflineTranslateRunner(store, aid, meta).run(NS(backend=backend),
                                                           asyncio.get_running_loop())
    asyncio.run(run())
    scene_calls = [c for c in calls if NOTE in c["prompt"]]
    assert len(scene_calls) == 1
    p = scene_calls[0]["prompt"]
    assert p.index(LINES[0][3]) < p.index(LINES[1][3]) < p.index(LINES[2][3])
    assert "(1) The player is speaking TO:" in p and TALK not in p
    rows = store.results_since(0)
    scene_rows = [r for r in rows if r["string_id"] in (10, 20, 30)]
    assert len(scene_rows) == 3
    traces = store.get_traces({r["trace_id"] for r in scene_rows})
    assert len(traces) == 1
    t = next(iter(traces.values()))
    assert t["kind"] == "translate" and t["string_ids"] == [30, 10, 20]
    store.close()
    db.close()


# ── хост: пометка сцены и дополнение темы ─────────────────────────────────────

def _graph():
    answers = ["audit.esp:000002", "audit.esp:000003"]
    return {"topics": {TOPIC: answers}, "prev": {}, "alias": {},
            "of_topic": {a: TOPIC for a in answers}}


@pytest.fixture
def host(tmp_path, monkeypatch):
    from translator.web.routes import jobs as J
    from translator.web import offline_backend as O
    from translator.characters import speakers, dialogue
    from translator.validation import official_context
    from translator.context import mod_summary
    db = TranslationDB(tmp_path / "host.db")
    repo = StringRepo(db)
    ids = {}
    # Порядок вставки нарочно не порядок проигрывания.
    for pos in (2, 0, 1):
        fid, rec, field, en = LINES[pos]
        key = f"('{fid}', '{rec}', '{field}', 0, 0)"
        repo.upsert("AuditMod", "Audit.esp", key, en, f"Хранимое {pos}", "translated", 90,
                    form_id=fid, rec_type=rec, field_type=field, source="ai",
                    string_hash=compute_hash(en))
        ids[pos] = repo.db.execute("SELECT id FROM strings WHERE key=?", (key,)).fetchone()[0]
    # Ответ из донорского перевода: в сцену не дописывается, машине не отдаётся.
    dk = "('05000004', 'INFO', 'NAM1', 0, 0)"
    repo.upsert("AuditMod", "Audit.esp", dk, "Donor line.", "Донорская.", "needs_review", 90,
                form_id="05000004", rec_type="INFO", field_type="NAM1",
                source="nexus-translation", string_hash=compute_hash("Donor line."))
    repo.upsert("AuditMod", "Audit.esp", "('05000009', 'WEAP', 'FULL', 0, 0)", "Iron Sword",
                "Железный меч", "translated", 90, form_id="05000009", rec_type="WEAP",
                field_type="FULL", source="ai", string_hash=compute_hash("Iron Sword"))
    graph = _graph()
    graph["topics"][TOPIC].append("audit.esp:000004")
    graph["of_topic"]["audit.esp:000004"] = TOPIC

    app = Flask(__name__)
    app.register_blueprint(J.bp)
    jm = MagicMock()

    def create(**kw):
        j = Job(id="scene-review", name=kw["name"], job_type=kw["job_type"], params=kw["params"])
        kw["fn"](j)
        return j

    jm.create.side_effect = create
    app.config.update(TESTING=True, JOB_MANAGER=jm, STRING_REPO=repo,
                      TRANSLATOR_CFG=NS(paths=NS(mods_dir=tmp_path / "mods")),
                      WORKER_REGISTRY=MagicMock())
    monkeypatch.setattr(J, "_resolve_backends", lambda *a: ([("audit-agent", None)], []))
    monkeypatch.setattr(speakers, "load", lambda *a, **k: {})
    monkeypatch.setattr(dialogue, "load", lambda *a, **k: graph)
    monkeypatch.setattr(official_context, "build_examples", lambda *a: {})
    monkeypatch.setattr(mod_summary, "build", lambda *a: "")
    dispatch = MagicMock()
    monkeypatch.setattr(O, "dispatch_multi", dispatch)
    yield NS(client=app.test_client(), dispatch=dispatch, ids=ids, repo=repo)
    db.close()


def _post(host, **options):
    r = host.client.post("/jobs/create", json={
        "type": "review_strings",
        "options": {"scope": "sweep", "machines": ["audit-agent"], **options}})
    assert r.status_code == 200, r.data
    host.dispatch.assert_called_once()
    mods = host.dispatch.call_args.args[1]
    items = [s for _mod, strs, _ctx in mods for s in strs]
    return items, host.dispatch.call_args.kwargs.get("extra")


def test_host_marks_scene_and_position(host):
    items, extra = _post(host, scene=True)
    got = {s["id"]: (s.get("scene"), s.get("scene_pos")) for s in items}
    assert got[host.ids[0]] == (TOPIC, 0)
    assert got[host.ids[1]] == (TOPIC, 1)
    assert got[host.ids[2]] == (TOPIC, 2)
    sword = [s for s in items if s["original"] == "Iron Sword"][0]
    assert "scene" not in sword and "scene_pos" not in sword
    assert extra and extra.get("scene") is True
    # Остальное — как всегда, по строке: разговор-подсказка хост кладёт по-прежнему,
    # а не прикладывает его внутри сцены агент.
    assert all(s.get("talk") for s in items if s.get("scene"))


def test_host_scene_expand_adds_the_rest_of_the_topic(host):
    items, extra = _post(host, scene=True, scene_expand=True, judge=True,
                         string_ids=[host.ids[1]])
    got = {s["id"]: s.get("scene_pos") for s in items}
    assert got == {host.ids[0]: 0, host.ids[1]: 1, host.ids[2]: 2}
    assert all(s["scene"] == TOPIC for s in items)
    assert "Donor line." not in {s["original"] for s in items}
    # Добавленные строки собираются тем же сборщиком: соперник для судьи — у каждой.
    assert {s["id"]: s.get("rival") for s in items} == {
        host.ids[0]: "Хранимое 0", host.ids[1]: "Хранимое 1", host.ids[2]: "Хранимое 2"}
    assert extra["scene"] is True and extra["judge"] is True


def test_host_without_option_sends_no_scene(host):
    items, extra = _post(host, string_ids=[host.ids[1]])
    assert [s["id"] for s in items] == [host.ids[1]]
    assert all("scene" not in s and "scene_pos" not in s for s in items)
    assert not (extra or {}).get("scene")
