"""Сквозные гарантии между подсистемами — стенд аудита 27.09, перенесённый в обычный набор.

No real model, network, production database or game files are mutated. The MLX
library boundary is replaced, NOT the production prompt builder/runner/backend.
Run explicitly; this directory is outside the normal tests/ discovery root.
"""
import asyncio
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
from models.mlx_backend import MlxBackend
from models.base import ModelState
from models.inference_params import InferenceParams as AgentParams
from translator.models.inference_params import InferenceParams
from translator.db.database import TranslationDB
from translator.db.repo import StringRepo
from translator.db import candidates as C, promote as P
from translator.data_manager.string_manager import StringManager
from translator.jobs.assignment_store import AssignmentStore
from translator.web.offline_backend import dispatch_multi, _make_remote_strings, dedupe_by_text
from translator.web.worker_registry import WorkerRegistry, WorkerInfo
from translator.web.job_manager import Job
from translator.web.pull_reconcile import apply_pulled_results


KEY = "('05000001', 'INFO', 'NAM1', 0, 0)"
EN = "I know this place and remember the old road."
RU = "Я знаю это место и помню старую дорогу."


@pytest.fixture
def repo(tmp_path):
    db = TranslationDB(tmp_path / "host.db")
    r = StringRepo(db)
    r.upsert("AuditMod", "Audit.esp", KEY, EN, RU, "translated", 100,
             form_id="05000001", rec_type="INFO", field_type="NAM1", source="ai",
             string_hash=compute_hash(EN))
    C.set_layer_only(r, True)
    yield r
    db.close()


@pytest.fixture
def store(tmp_path):
    s = ResultStore(tmp_path / "agent.db")
    yield s
    s.close()


def item(**changes):
    return dict(id=1, key=KEY, esp="Audit.esp", mod_name="AuditMod",
                original=EN, rec_type="INFO", **changes)


def seed(store, items, **meta):
    from remote_server import _persist_offline_chunk
    package = dict(offline_job_id="audit-assignment", host_job_id="audit-job",
                   strings=items, params={"batch_size": 8}, **meta)
    _persist_offline_chunk(NS(result_store=store), json.loads(json.dumps(package)))
    # Read metadata back exactly as a restarted worker does.
    return json.loads(store.get_assignment("audit-assignment")["params_json"])


class Backend:
    def __init__(self, answers=None):
        self.answers = iter(answers or ["1. " + RU])
        self.calls = []
        self.last_finish_reason = "stop"

    def _infer(self, prompt, params=None, stop_check=None):
        self.calls.append(prompt)
        answer = next(self.answers, "")
        if isinstance(answer, tuple):
            answer, self.last_finish_reason = answer
        return answer


def produce(store, meta, backend):
    async def run():
        await OfflineTranslateRunner(store, "audit-assignment", meta).run(
            NS(backend=backend), asyncio.get_running_loop())
    asyncio.run(run())
    return store.results_since(0)


def mlx_probe(monkeypatch, answers=None):
    """Use the real MlxBackend up to mlx_lm.stream_generate/generate."""
    calls = []
    answers = iter(answers or ["1. " + RU])
    lib = ModuleType("mlx_lm")
    samples = ModuleType("mlx_lm.sample_utils")
    samples.make_sampler = lambda **kw: kw
    samples.make_logits_processors = lambda **kw: kw

    def stream(model, tokenizer, **kw):
        calls.append(kw)
        yield NS(text=next(answers, ""), finish_reason="stop")

    def generate(model, tokenizer, **kw):
        calls.append(kw)
        return next(answers, "")

    lib.stream_generate = stream
    lib.generate = generate
    monkeypatch.setitem(sys.modules, "mlx_lm", lib)
    monkeypatch.setitem(sys.modules, "mlx_lm.sample_utils", samples)
    b = MlxBackend(NS(repo_id="audit", temperature=.3, top_p=.9,
                      repetition_penalty=1.05, max_new_tokens=100, batch_size=8))
    b._state = ModelState.LOADED
    b._model, b._tokenizer = object(), object()
    return b, calls


def test_host_package_restart_runner_mlx_receives_all_prompt_blocks(repo, store, monkeypatch):
    from translator.web import offline_backend as O
    monkeypatch.setattr(O, "_build_terminology", lambda _: "GLOSSARY_SENTINEL")
    registry = WorkerRegistry()
    registry.register(WorkerInfo(label="audit-agent", url="http://unused"))
    job = Job(id="audit-job", name="audit", job_type="translate_strings")
    cfg = NS(translation=NS(source_lang="English", target_lang="Russian"))
    i = item(speaker="SPEAKER_SENTINEL", style="STYLE_SENTINEL",
             entities="ENTITY_SENTINEL", talk="TALK_SENTINEL", rival="RIVAL_SENTINEL")
    i["original"] = EN * 4  # long enough for the intentionally conditional mod summary
    captured = []
    registry.enqueue_chunk = lambda label, chunk: captured.append(chunk)
    dispatch_multi(job, [("AuditMod", [i], "MOD_SUMMARY_SENTINEL")],
                   InferenceParams(system_prompt="SYSTEM_SENTINEL", max_tokens=333),
                   [("audit-agent", None)], registry, MagicMock(), repo, cfg,
                   extra={"judge": True})
    from remote_server import _persist_offline_chunk
    _persist_offline_chunk(NS(result_store=store), json.loads(json.dumps(captured[0])))
    aid = captured[0]["offline_job_id"]
    meta = json.loads(store.get_assignment(aid)["params_json"])
    backend, calls = mlx_probe(monkeypatch, ["1. " + RU, "B", "A"])

    async def run():
        await OfflineTranslateRunner(store, aid, meta).run(NS(backend=backend), asyncio.get_running_loop())
    asyncio.run(run())
    prompt = calls[0]["prompt"]
    for marker in ["SYSTEM", "GLOSSARY", "SPEAKER", "STYLE", "ENTITY", "TALK", "MOD_SUMMARY"]:
        assert marker + "_SENTINEL" in prompt
    assert "RIVAL_SENTINEL" not in prompt
    assert calls[0]["max_tokens"] == 333
    assert "RIVAL_SENTINEL" in calls[1]["prompt"]
    assert store.results_since(0)[0]["judge"] == "fresh"


def test_term_requirement_reaches_model_after_persistence(store):
    wire, _ = _make_remote_strings([item(current="OLD_SENTINEL", req_terms="Name = TERM_SENTINEL")], "AuditMod")
    meta = seed(store, wire)
    b = Backend()
    produce(store, meta, b)
    assert "OLD_SENTINEL" in b.calls[0] and "TERM_SENTINEL" in b.calls[0]
    assert "Required rendering" in b.calls[0]


@pytest.mark.parametrize("name,value", [("top_k", 7), ("repetition_penalty", 1.17)])
def test_mlx_honors_sampling_overrides(monkeypatch, name, value):
    b, calls = mlx_probe(monkeypatch)
    b._infer("PROMPT_SENTINEL", AgentParams(**{name: value}), stop_check=lambda: False)
    got = calls[0]
    if name == "top_k":
        assert got["sampler"].get("top_k") == value
    else:
        assert got.get("logits_processors", {}).get("repetition_penalty") == value


def test_short_strings_keep_speaker_and_dialogue(store):
    i = item(speaker="SPEAKER_SENTINEL", talk="TALK_SENTINEL")
    i["original"] = "Hello."
    meta = seed(store, [i], context="MOD_SUMMARY_SENTINEL")
    b = Backend()
    produce(store, meta, b)
    assert "SPEAKER_SENTINEL" in b.calls[0] and "TALK_SENTINEL" in b.calls[0]
    assert "MOD_SUMMARY_SENTINEL" not in b.calls[0]


def test_contexts_are_not_deduplicated_away():
    rows = [item(speaker="male"), item(speaker="female")]
    rows[1]["id"] = 2
    assert len(dedupe_by_text(rows)[0]) == 2


def test_field_type_survives_manifest(store):
    wire, _ = _make_remote_strings([item(field_type="NAM1")], "AuditMod")
    seed(store, wire)
    assert store.pending_items("audit-assignment")[0].get("field_type") == "NAM1"


def test_each_record_type_gets_its_own_style(store):
    a = item(style="INFO_STYLE_SENTINEL")
    b = item(style="BOOK_STYLE_SENTINEL")
    b.update(id=2, original="The book describes a distant village.", rec_type="BOOK")
    meta = seed(store, [a, b])
    backend = Backend(["1. " + RU + "\n2. Книга описывает далёкую деревню."])
    produce(store, meta, backend)
    assert any("BOOK_STYLE_SENTINEL" in p for p in backend.calls)


def test_context_number_is_rebound_on_single_retry(store):
    a = item(talk="FIRST_LINE_CONTEXT")
    b = item(talk="SECOND_LINE_CONTEXT")
    b.update(id=2, original="I remember the bridge.")
    meta = seed(store, [a, b])
    backend = Backend(["1. " + RU, "1. " + RU, "1. Я помню мост."])
    produce(store, meta, backend)
    assert "(1) SECOND_LINE_CONTEXT" in backend.calls[2]
    assert "FIRST_LINE_CONTEXT" not in backend.calls[2]


@pytest.mark.parametrize("raw", ["Both translations are equally good.", "garbage", "A or B"])
def test_judge_rejects_malformed_output(store, raw):
    b = Backend([raw, "A"])
    async def run():
        return await OfflineTranslateRunner(store, "unused", {})._judge(
            NS(backend=b), asyncio.get_running_loop(), EN, "old", "new", AgentParams())
    assert asyncio.run(run()) == "unsure"


def test_failed_assignment_stays_resumable(store):
    from remote_server import _produce_assignment
    meta = seed(store, [item()])
    async def run():
        await _produce_assignment(NS(result_store=store, backend=Backend([""] * 20)),
                                  asyncio.get_running_loop(), "audit-assignment", meta)
    asyncio.run(run())
    assert store.pending_items("audit-assignment")
    assert store.get_assignment("audit-assignment")["state"] != "complete"


def mgr(repo, tmp_path):
    m = StringManager(repo, tmp_path)
    m._terms = {}
    return m


def result(**changes):
    r = dict(seq=1, assignment_id="audit-assignment", string_id=1, original=EN,
             translation=RU, key=KEY, esp_name="Audit.esp", mod_name="AuditMod",
             string_hash=compute_hash(EN), produced_at=123.0, status="translated",
             quality_score=100, judge="fresh", rival="OLD_SENTINEL")
    r.update(changes)
    return r


def test_pull_keeps_candidate_and_preserves_corpus(repo, tmp_path):
    r = result(translation="Я помню это место и старую дорогу.")
    apply_pulled_results(mgr(repo, tmp_path), None, "audit-agent", [r])
    assert repo.db.execute("SELECT translation FROM strings WHERE id=1").fetchone()[0] == RU
    assert repo.db.execute("SELECT translation FROM candidates").fetchone()[0] == r["translation"]


def test_generation_limit_survives_agent_to_candidate(repo, store, tmp_path):
    from remote_server import _row_to_result
    meta = seed(store, [item()])
    rs = produce(store, meta, Backend([("1. " + RU, "length")]))
    assert rs[0]["status"] == "needs_review"
    apply_pulled_results(mgr(repo, tmp_path), None, "audit-agent", [_row_to_result(r) for r in rs])
    row = repo.db.execute("SELECT * FROM candidates").fetchone()
    assert row["rules_status"] == "needs_review", "model stop evidence disappeared"


def test_pull_does_not_advance_past_failed_candidate(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(C, "record", lambda *a, **k: None)
    _, _, cursor, _ = apply_pulled_results(mgr(repo, tmp_path), None, "audit-agent", [result(seq=42)])
    assert cursor < 42


def test_pull_exception_does_not_bypass_candidate_layer(repo, tmp_path, monkeypatch):
    def fail(*a, **k):
        raise RuntimeError("injected candidate schema failure")
    monkeypatch.setattr(C, "record", fail)
    m = mgr(repo, tmp_path)
    m.save_string = MagicMock()
    apply_pulled_results(m, None, "audit-agent", [result()])
    m.save_string.assert_not_called()


def push_client(repo, tmp_path):
    from translator.web.routes.api import bp
    app = Flask(__name__)
    app.register_blueprint(bp)
    reg = WorkerRegistry()
    reg.register(WorkerInfo(label="audit-agent", url="http://unused", model="current-model"))
    reg.register_offline_job("audit-assignment", "audit-job", "audit-agent", 1)
    jm = MagicMock()
    jm.get_job.return_value = None
    app.config.update(TESTING=True, WORKER_REGISTRY=reg, JOB_MANAGER=jm, STRING_REPO=repo,
                      TRANSLATOR_CFG=NS(paths=NS(mods_dir=tmp_path)))
    return app.test_client()


def test_push_failure_not_acknowledged(repo, tmp_path, monkeypatch):
    client = push_client(repo, tmp_path)
    monkeypatch.setattr(C, "record", lambda *a, **k: None)
    response = client.post("/api/workers/audit-agent/offline-results", json={
        "offline_job_id": "audit-assignment", "results": [result()], "batch_max_seq": 1})
    assert response.status_code == 200
    assert response.json["failed_seqs"] == [1] and response.json["confirmed_seq"] == 0


def test_push_and_pull_use_same_job_identity(repo, tmp_path):
    client = push_client(repo, tmp_path)
    response = client.post("/api/workers/audit-agent/offline-results", json={
        "offline_job_id": "audit-assignment", "results": [result()], "batch_max_seq": 1})
    assert response.status_code == 200
    apply_pulled_results(mgr(repo, tmp_path), None, "audit-agent", [result(produced_at=124.0)])
    jobs = {r[0] for r in repo.db.execute("SELECT job_id FROM candidates")}
    assert jobs == {"audit-job"}


def test_original_mismatch_cannot_be_promoted(repo, tmp_path):
    r = result(original="A different source text.", string_hash=compute_hash("A different source text."))
    apply_pulled_results(mgr(repo, tmp_path), None, "audit-agent", [r])
    rows = repo.db.execute("SELECT * FROM candidates").fetchall()
    assert not rows or rows[0]["rules_status"] != "translated"


def test_official_authority_applies_to_promoted_source():
    from translator.validation.authority import official_override
    assert official_override("Fort Dawnguard", "Wrong text", "02000001", "ai-judged",
                             {"Fort Dawnguard": "Official text"}) == "Official text"


def test_termfix_rejects_unrelated_insertions():
    assert P._changed_share("one two three", "one two three four five six seven eight") > .34


def test_layer_setting_read_failure_does_not_enable_apply():
    db = MagicMock()
    db.get_setting.side_effect = RuntimeError("injected read failure")
    try:
        enabled = C.layer_only(NS(db=db))
    except Exception:
        return  # stopping the operation is also safe
    assert enabled is True


def test_duplicate_write_has_history(repo):
    repo.apply_correction_to_duplicates(compute_hash(EN), RU, "Я помню старую дорогу.", "translated", 100)
    assert repo.db.execute("SELECT COUNT(*) FROM string_history WHERE string_id=1").fetchone()[0] > 0


def test_checkpoint_restores_provenance(repo):
    checkpoint = repo.create_checkpoint("AuditMod")
    repo.db.execute("UPDATE strings SET translation='replacement',source='duplicate' WHERE id=1")
    repo.db.commit()
    repo.restore_checkpoint(checkpoint)
    assert repo.db.execute("SELECT source FROM strings WHERE id=1").fetchone()[0] == "ai"


def test_blind_translation_does_not_receive_its_rival_through_memory(store):
    i = item(rival="RIVAL_SENTINEL")
    i["original"] = "Whiterun"
    meta = seed(store, [i], tm_pairs={"Whiterun": "RIVAL_SENTINEL"}, judge=True)
    b = Backend(["1. Вайтран", "B", "A"])
    produce(store, meta, b)
    assert "RIVAL_SENTINEL" not in b.calls[0]


def test_rejected_translation_is_not_used_as_translation_memory(repo):
    from translator.web.offline_backend import _build_tm_pairs
    repo.db.execute("UPDATE strings SET status='needs_review',translation='BROKEN_SENTINEL'")
    repo.db.commit()
    assert "BROKEN_SENTINEL" not in _build_tm_pairs(repo, "AuditMod").values()


def test_entities_for_every_line_reach_generation(store):
    rows = []
    for n in range(5):
        i = item(entities=f"ENTITY_{n}_SENTINEL")
        i.update(id=n+1, original=f"The traveller remembers village number {n}.")
        rows.append(i)
    meta = seed(store, rows)
    b = Backend(["\n".join(f"{n}. Путник помнит деревню." for n in range(1, 6))])
    produce(store, meta, b)
    assert any("ENTITY_4_SENTINEL" in p for p in b.calls)


def test_each_single_retry_retains_its_own_finish_reason(store):
    a, b = item(), item()
    b.update(id=2, original="I remember the bridge.")
    meta = seed(store, [a, b])
    rs = produce(store, meta, Backend(["1. " + RU, ("1. " + RU, "length"),
                                     ("1. Я помню мост.", "stop")]))
    assert rs[0]["status"] == "needs_review"


def test_mcm_apply_preserves_database_translation(repo, tmp_path, monkeypatch):
    from translator.parsing.mcm_handler import write, read
    from translator.pipeline.apply_pipeline import ApplyPipeline
    from translator.web.job_manager import JobManager
    from scripts import translate_mcm as M
    import translator.pipeline as pipeline

    mod = tmp_path / "mods" / "AuditMod"
    rel = "interface/translations/audit_english.txt"
    write(mod / rel, [("$Audit", "Hello traveller")])
    repo.upsert("AuditMod", "audit_english.txt", f"mcm:{rel}:0:$Audit",
                "Hello traveller", "ПЕРЕВОД ИЗ БАЗЫ", "translated", 100)
    cfg = NS(paths=NS(mods_dir=mod.parent, temp_dir=tmp_path / "assets", bsarch_exe=None,
                      ffdec_jar=None, backup_dir=tmp_path / "backups"),
             ensemble=NS(model_a=NS(batch_size=8)))
    monkeypatch.setattr(JobManager, "get", staticmethod(lambda: MagicMock()))
    monkeypatch.setattr(M, "_get_cfg", lambda: cfg)
    monkeypatch.setattr(M, "_paths", lambda: cfg.paths)
    monkeypatch.setattr(pipeline, "get_mod_context", lambda _: "", raising=False)
    inference = MagicMock(return_value=["НОВЫЙ ОТВЕТ МОДЕЛИ"])
    monkeypatch.setattr(M, "translate_batch", inference)
    job = Job(id="apply-test", name="audit", job_type="translate_bsa")
    ApplyPipeline(cfg, repo).run_bsa(job, "AuditMod")
    pairs, _ = read(mod / "interface/translations/audit_russian.txt")
    assert dict(pairs)["$Audit"] == "ПЕРЕВОД ИЗ БАЗЫ", f"unexpected inference calls: {inference.call_count}"


def test_loose_swf_only_mod_reaches_export(repo, tmp_path, monkeypatch):
    from translator.pipeline.apply_pipeline import ApplyPipeline
    from translator.web.job_manager import JobManager
    from translator.parsing import asset_extractor as A
    mod = tmp_path / "mods" / "AuditMod"
    mod.mkdir(parents=True)
    (mod / "audit.swf").write_bytes(b"audit fixture, not parsed")
    monkeypatch.setattr(JobManager, "get", staticmethod(lambda: MagicMock()))
    exported = MagicMock()
    monkeypatch.setattr(A, "apply_all_assets", exported)
    cfg = NS(paths=NS(mods_dir=mod.parent, temp_dir=tmp_path / "assets", bsarch_exe=None, ffdec_jar=None))
    ApplyPipeline(cfg, repo).run_bsa(Job(id="swf-test", name="audit", job_type="translate_bsa"), "AuditMod")
    exported.assert_called_once()


def test_push_and_pull_delivery_deduplicate_same_answer(repo, tmp_path):
    client = push_client(repo, tmp_path)
    for _ in range(2):
        response = client.post("/api/workers/audit-agent/offline-results", json={
            "offline_job_id": "audit-assignment", "results": [result()], "batch_max_seq": 1})
        assert response.status_code == 200
    apply_pulled_results(mgr(repo, tmp_path), None, "audit-agent", [result()])
    assert repo.db.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 1
    assert repo.db.execute("SELECT translation FROM strings WHERE id=1").fetchone()[0] == RU


def test_separate_speakers_are_batched_separately_when_not_deduplicated(store):
    a, b = item(speaker="SPEAKER_ONE_SENTINEL"), item(speaker="SPEAKER_TWO_SENTINEL")
    b.update(id=2, original="I remember the bridge.")
    meta = seed(store, [a, b])
    backend = Backend(["1. " + RU, "1. Я помню мост."])
    produce(store, meta, backend)
    assert len(backend.calls) == 2
    assert "SPEAKER_TWO_SENTINEL" not in backend.calls[0]
    assert "SPEAKER_ONE_SENTINEL" not in backend.calls[1]


def test_legacy_public_pipeline_api_is_importable():
    import translator.pipeline as pipeline
    assert callable(getattr(pipeline, "translate_batch", None))
    assert callable(getattr(pipeline, "get_mod_context", None))


def test_swf_import_replaces_original_with_translated_file(tmp_path, monkeypatch):
    from translator.pipeline.apply_pipeline import _translate_swf_texts
    from translator.parsing import swf_handler
    from scripts import esp_engine
    import translator.pipeline as pipeline
    mod = tmp_path / "mods" / "AuditMod"
    mod.mkdir(parents=True)
    swf = mod / "audit.swf"
    swf.write_bytes(b"original-swf")
    cfg = NS(paths=NS(mods_dir=mod.parent, backup_dir=tmp_path / "backup"))

    def export(jar, source, texts_dir):
        (texts_dir / "1.txt").write_text("0 | Hello traveller", encoding="utf-8")

    def import_(jar, source, texts_dir, dest):
        dest.write_bytes(b"translated-swf")

    monkeypatch.setattr(swf_handler, "export_texts", export)
    monkeypatch.setattr(swf_handler, "import_texts", import_)
    monkeypatch.setattr(pipeline, "get_mod_context", lambda _: "", raising=False)
    monkeypatch.setattr(esp_engine, "translate_texts", lambda *a, **k: [
        {"translation": "Привет, путник", "skipped": False, "token_issues": []}])
    _translate_swf_texts(Job(id="swf", name="audit", job_type="translate_bsa"), swf, "fake.jar", cfg)
    assert swf.exists(), "original path disappeared instead of being replaced"
    assert swf.read_bytes() == b"translated-swf"


def test_swf_uses_configured_paths_ffdec(repo, tmp_path, monkeypatch):
    from translator.pipeline import apply_pipeline as A
    from translator.web.job_manager import JobManager
    from translator.parsing import asset_extractor
    from translator.parsing.mcm_handler import write
    from scripts import translate_mcm
    mod = tmp_path / "mods" / "AuditMod"
    write(mod / "interface/translations/audit_english.txt", [("$A", "Hello")])
    (mod / "audit.swf").write_bytes(b"not parsed")
    jar = tmp_path / "configured.jar"
    jar.write_bytes(b"not executed")
    cfg = NS(paths=NS(mods_dir=mod.parent, temp_dir=tmp_path / "assets", bsarch_exe=None,
                      ffdec_jar=jar))
    monkeypatch.setattr(JobManager, "get", staticmethod(lambda: MagicMock()))
    monkeypatch.setattr(asset_extractor, "apply_all_assets", lambda *a: (0, 0, 0))
    monkeypatch.setattr(translate_mcm, "cmd_translate_mcm", lambda *a, **k: None)
    # Контракт уточнён 27.09: ветка SWF обязана исполниться по настроенному
    # paths.ffdec_jar, но применение НЕ генерирует — без строк в базе SWF пропускается.
    generated = MagicMock()
    monkeypatch.setattr(A, "_translate_swf_texts", generated)
    reached = MagicMock(return_value=False)
    monkeypatch.setattr(A.ApplyPipeline, "_import_swf_from_db", reached)
    A.ApplyPipeline(cfg, repo).run_bsa(Job(id="swf", name="audit", job_type="translate_bsa"), "AuditMod")
    reached.assert_called_once()
    generated.assert_not_called()


def test_delayed_result_records_producer_model_not_current_worker_model(repo, tmp_path):
    client = push_client(repo, tmp_path)
    response = client.post("/api/workers/audit-agent/offline-results", json={
        "offline_job_id": "audit-assignment", "results": [result(model="producer-model")],
        "batch_max_seq": 1})
    assert response.status_code == 200
    assert repo.db.execute("SELECT model FROM candidates").fetchone()[0] == "producer-model"


def test_review_http_params_reach_dispatch(repo, tmp_path, monkeypatch):
    from translator.web.routes import jobs as J
    from translator.web import offline_backend as O
    from translator.characters import speakers, dialogue
    from translator.validation import official_context
    from translator.context import mod_summary
    app = Flask(__name__)
    app.register_blueprint(J.bp)
    cfg = NS(paths=NS(mods_dir=tmp_path / "mods"))
    jm = MagicMock()

    def create(**kw):
        j = Job(id="audit-review", name=kw["name"], job_type=kw["job_type"], params=kw["params"])
        kw["fn"](j)
        return j

    jm.create.side_effect = create
    app.config.update(TESTING=True, JOB_MANAGER=jm, STRING_REPO=repo,
                      TRANSLATOR_CFG=cfg, WORKER_REGISTRY=MagicMock())
    monkeypatch.setattr(J, "_resolve_backends", lambda *a: ([("audit-agent", None)], []))
    monkeypatch.setattr(speakers, "load", lambda *a, **k: {})
    monkeypatch.setattr(dialogue, "load", lambda *a, **k: {})
    monkeypatch.setattr(official_context, "build_examples", lambda *a: {})
    monkeypatch.setattr(mod_summary, "build", lambda *a: "")
    dispatch = MagicMock()
    monkeypatch.setattr(O, "dispatch_multi", dispatch)
    response = app.test_client().post("/jobs/create", json={
        "type": "review_strings", "options": {"scope": "sweep", "machines": ["audit-agent"]},
        "params": {"system_prompt": "SYSTEM_SENTINEL", "temperature": .71, "max_tokens": 1234}})
    assert response.status_code == 200
    dispatch.assert_called_once()
    passed = dispatch.call_args.args[2]
    assert passed.system_prompt == "SYSTEM_SENTINEL"
    assert passed.temperature == .71 and passed.max_tokens == 1234
