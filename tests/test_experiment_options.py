"""Опции раздачи для экспериментов — через настоящий /jobs/create.

Стенд гоняет опыты боевым путём, а не ручным /infer (аудит 27.09: ручной запрос не
проверяет сборку пакета). Для этого раздача должна уметь три вещи, и каждая здесь
проверяется по тому, что реально ушло в dispatch_multi:

    string_ids     ровно эти строки, в любом статусе — ветки опыта получают одно и то же;
    context_parts  только названные части контекста — ветки различаются одной частью;
    trace_full     полный промпт каждого вызова — в трассу.
"""
import sys
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

from flask import Flask

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from translator.db.database import TranslationDB   # noqa: E402
from translator.db.repo import StringRepo          # noqa: E402
from translator.web.job_manager import Job         # noqa: E402


def _repo(tmp_path):
    r = StringRepo(TranslationDB(tmp_path / "host.db"))
    for i, (en, ru, st) in enumerate([("First line of the test.", "Первая строка.", "translated"),
                                      ("Second line of the test.", "Вторая строка.", "needs_review"),
                                      ("Third line of the test.", "Третья строка.", "translated")],
                                     1):
        r.upsert("Mod", "Mod.esp", f"k{i}", en, ru, st, 100, form_id=f"0500000{i}",
                 rec_type="INFO", field_type="NAM1", source="ai")
    return r


def _dispatch(tmp_path, monkeypatch, options):
    from translator.characters import dialogue, speakers
    from translator.context import mod_summary
    from translator.validation import official_context
    from translator.web import offline_backend as O
    from translator.web.routes import jobs as J
    repo = _repo(tmp_path)
    app = Flask(__name__)
    app.register_blueprint(J.bp)
    jm = MagicMock()

    def create(**kw):
        j = Job(id="exp", name=kw["name"], job_type=kw["job_type"], params=kw["params"])
        kw["fn"](j)
        return j

    jm.create.side_effect = create
    app.config.update(TESTING=True, JOB_MANAGER=jm, STRING_REPO=repo,
                      TRANSLATOR_CFG=NS(paths=NS(mods_dir=tmp_path / "mods")),
                      WORKER_REGISTRY=MagicMock())
    monkeypatch.setattr(J, "_resolve_backends", lambda *a: ([("bench", None)], []))
    monkeypatch.setattr(speakers, "load", lambda *a, **k: {"voice": {}, "cards": {}})
    monkeypatch.setattr(speakers, "block_for",
                        lambda *a, vocab=True: "SPEAKER_CARD" + ("+VOCAB" if vocab else ""))
    monkeypatch.setattr(dialogue, "load", lambda *a, **k: {})
    monkeypatch.setattr(official_context, "build_examples", lambda *a: {"INFO": [("a", "б")]})
    monkeypatch.setattr(official_context, "style_block", lambda *a: "STYLE_BLOCK")
    monkeypatch.setattr(official_context, "entity_block", lambda *a: "ENTITY_BLOCK")
    monkeypatch.setattr(official_context, "analog_block", lambda *a: "ANALOG_BLOCK")
    monkeypatch.setattr(mod_summary, "build", lambda *a: "MOD_SUMMARY")
    dispatch = MagicMock()
    monkeypatch.setattr(O, "dispatch_multi", dispatch)
    resp = app.test_client().post("/jobs/create", json={
        "type": "review_strings", "options": {"scope": "sweep", "machines": ["bench"],
                                              **options}})
    assert resp.status_code == 200, resp.json
    dispatch.assert_called_once()
    mods = dispatch.call_args.args[1]
    extra = dispatch.call_args.kwargs.get("extra") or {}
    items = [s for _m, strs, _c in mods for s in strs]
    return mods, items, extra


def test_exactly_the_named_strings_go_out_in_any_status(tmp_path, monkeypatch):
    _mods, items, _ = _dispatch(tmp_path, monkeypatch, {"string_ids": [2, 3]})
    assert sorted(i["id"] for i in items) == [2, 3], "needs_review и translated — оба"


def test_the_production_profile_by_default(tmp_path, monkeypatch):
    # Не заданный состав контекста — это боевой профиль из замера, а не «всё подряд»:
    # аналоги к строке, без сводки мода и без пакетного блока терминов.
    mods, items, extra = _dispatch(tmp_path, monkeypatch, {"string_ids": [1]})
    assert items[0]["speaker"] == "SPEAKER_CARD+VOCAB" and items[0]["style"] == "STYLE_BLOCK"
    assert items[0]["entities"] == "ENTITY_BLOCK\nANALOG_BLOCK"
    assert mods[0][2] == "", "сводка мода поверх аналогов не дала ничего"
    assert extra.get("terminology") == "" and "tm_pairs" not in extra


def test_only_the_named_context_parts_survive(tmp_path, monkeypatch):
    mods, items, extra = _dispatch(tmp_path, monkeypatch,
                                   {"string_ids": [1], "context_parts": ["speaker"]})
    it = items[0]
    assert it.get("speaker") == "SPEAKER_CARD", "словарь персонажа — отдельная часть"
    assert "style" not in it and "entities" not in it and "talk" not in it
    assert mods[0][2] == "", "сводка мода выключена"
    assert extra.get("terminology") == "" and extra.get("tm_pairs") == {}


def test_full_trace_is_requested_through_the_package(tmp_path, monkeypatch):
    _m, _i, extra = _dispatch(tmp_path, monkeypatch, {"string_ids": [1], "trace_full": True})
    assert extra.get("trace_full") is True
