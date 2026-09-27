"""
ApplyPipeline.run_bsa — применение MCM / BSA-MCM / SWF из БД в файлы мода.

Аудит (audit/runtime_validation, раздел «Экспорт в игровые файлы») нашёл на этом
пути пять поломок, каждая из которых снаружи выглядела как успешный проход:

1. после экспорта из БД run_bsa звал cmd_translate_mcm, который заново переводил
   *_english.txt моделью и переписывал только что выгруженный *_russian.txt;
2. translator/pipeline.py был затенён пакетом translator/pipeline/, и
   translate_batch / get_mod_context через `translator.pipeline` не импортировались;
3. мод, где есть только loose .swf, выходил из run_bsa до экспорта;
4. FFDec читался из cfg.tools.ffdec_jar, а конфигурация хранит cfg.paths.ffdec_jar;
5. после FFDec-импорта оригинал переносился поверх переведённого файла.

Тесты ниже исполняют сам путь на временных файлах; внешние инструменты (BSArch,
FFDec) подменены на уровне subprocess.run, так что проверяется наш код, а не их.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest

from translator.db.database import TranslationDB
from translator.db.repo import StringRepo
from translator.parsing.mcm_handler import read as mcm_read, write as mcm_write
from translator.web.job_manager import Job, JobManager

MOD = "AuditMod"
REL = "interface/translations/audit_english.txt"


@pytest.fixture
def repo(tmp_path):
    db = TranslationDB(tmp_path / "host.db")
    yield StringRepo(db)
    db.close()


@pytest.fixture(autouse=True)
def quiet_jobs(monkeypatch):
    monkeypatch.setattr(JobManager, "get", staticmethod(lambda: MagicMock()))


@pytest.fixture
def no_generation(monkeypatch):
    """Любая попытка сгенерировать перевод во время применения — провал теста."""
    import translator.pipeline as pipeline
    from scripts import esp_engine, translate_mcm

    def boom(*a, **k):
        raise AssertionError("apply triggered model generation")

    monkeypatch.setattr(pipeline, "translate_batch", boom)
    monkeypatch.setattr(translate_mcm, "translate_batch", boom)
    monkeypatch.setattr(esp_engine, "translate_texts", boom)
    monkeypatch.setattr(pipeline, "get_mod_context", lambda _: "")


def _cfg(tmp_path, **paths):
    base = dict(mods_dir=tmp_path / "mods", temp_dir=tmp_path / "assets",
                bsarch_exe=None, ffdec_jar=None, backup_dir=tmp_path / "backups")
    base.update(paths)
    return NS(paths=NS(**base), ensemble=NS(model_a=NS(batch_size=8)))


def _job():
    return Job(id="t", name="t", job_type="translate_bsa")


def _run(cfg, repo, **kw):
    from translator.pipeline.apply_pipeline import ApplyPipeline
    job = _job()
    ApplyPipeline(cfg, repo).run_bsa(job, MOD, **kw)
    return job


# ── 1. MCM: применение не генерирует ────────────────────────────────────────────

def test_loose_mcm_apply_writes_db_translation_and_never_generates(tmp_path, repo,
                                                                     no_generation):
    mod = tmp_path / "mods" / MOD
    mcm_write(mod / REL, [("$Greet", "Hello traveller"), ("$Bye", "Farewell")])
    repo.upsert(MOD, "audit_english.txt", f"mcm:{REL}:0:$Greet",
                "Hello traveller", "ПЕРЕВОД ИЗ БАЗЫ", "translated", 100)

    _run(_cfg(tmp_path), repo)

    pairs, _ = mcm_read(mod / "interface/translations/audit_russian.txt")
    # строка из базы доехала, строка без перевода осталась английской — модель не звали
    assert dict(pairs) == {"$Greet": "ПЕРЕВОД ИЗ БАЗЫ", "$Bye": "Farewell"}


def test_bsa_mcm_from_db_is_repacked_without_generation(tmp_path, repo, no_generation,
                                                        monkeypatch):
    """BSA-MCM: кэш *_russian.txt из БД накладывается на архив, подмена атомарная."""
    from translator.web import asset_cache

    mod = tmp_path / "mods" / MOD
    mod.mkdir(parents=True)
    bsa = mod / "Audit.bsa"
    bsa.write_bytes(b"ORIGINAL-BSA")
    bsarch = tmp_path / "BSArch.exe"
    bsarch.write_bytes(b"")
    cfg = _cfg(tmp_path, bsarch_exe=bsarch)

    # кэш, каким его оставляет seed_assets / ModScanner
    cache = asset_cache.BsaStringCache(cfg.paths.temp_dir, str(bsarch))
    cd = cache._cache_dir(MOD, bsa.name)
    mcm_write(cd / REL, [("$Greet", "Hello traveller")])
    repo.upsert(MOD, f"{bsa.name}/audit_english.txt",
                f"bsa-mcm:{bsa.name}:{REL}:0:$Greet",
                "Hello traveller", "ИЗ БАЗЫ В АРХИВ", "translated", 100)

    packed = {}

    def fake_run(cmd, **kw):
        if cmd[1] == "unpack":
            work = Path(cmd[3])
            mcm_write(work / REL, [("$Greet", "Hello traveller")])
        elif cmd[1] == "pack":
            src, out = Path(cmd[2]), Path(cmd[3])
            assert out != bsa, "BSArch must not write straight into the live archive"
            ru = src / "interface/translations/audit_russian.txt"
            packed.update(dict(mcm_read(ru)[0]))
            out.write_bytes(b"REPACKED-BSA")
        return NS(returncode=0, stdout="", stderr=b"")

    monkeypatch.setattr(asset_cache.subprocess, "run", fake_run)
    _run(cfg, repo)

    assert packed == {"$Greet": "ИЗ БАЗЫ В АРХИВ"}
    assert bsa.read_bytes() == b"REPACKED-BSA"
    assert (cfg.paths.backup_dir / MOD / "Audit.bsa").read_bytes() == b"ORIGINAL-BSA"


def test_failed_bsa_repack_leaves_live_archive_intact(tmp_path, monkeypatch):
    from translator.web import asset_cache

    mods = tmp_path / "mods"
    bsa = mods / MOD / "Audit.bsa"
    bsa.parent.mkdir(parents=True)
    bsa.write_bytes(b"ORIGINAL-BSA")
    bsarch = tmp_path / "BSArch.exe"
    bsarch.write_bytes(b"")
    cache = asset_cache.BsaStringCache(tmp_path / "assets", str(bsarch))
    mcm_write(cache._cache_dir(MOD, bsa.name) / "interface/translations/a_russian.txt",
              [("$A", "А")])

    def fake_run(cmd, **kw):
        if cmd[1] == "pack":
            Path(cmd[3]).write_bytes(b"HALF")      # обрубок, и BSArch сообщает ошибку
            return NS(returncode=1, stdout="", stderr=b"boom")
        return NS(returncode=0, stdout="", stderr=b"")

    monkeypatch.setattr(asset_cache.subprocess, "run", fake_run)
    assert cache.apply_to_bsa(bsa, MOD, mods, tmp_path / "backups") is False
    assert bsa.read_bytes() == b"ORIGINAL-BSA"
    assert not bsa.with_name(bsa.name + ".tmp").exists()


# ── 2. публичный API translator.pipeline ───────────────────────────────────────

def test_public_pipeline_api_reaches_the_ensemble(monkeypatch):
    """Не просто импорт: MCM-обёртка должна дойти до ансамбля, а не вернуть оригиналы."""
    import translator.pipeline as pipeline
    from scripts import translate_mcm
    # подпакеты пакета по-прежнему импортируются
    from translator.pipeline.apply_pipeline import ApplyPipeline  # noqa: F401
    from translator.pipeline.translate_pipeline import DeployMode  # noqa: F401

    seen = []

    class FakeEnsemble:
        def translate(self, texts, context="", **kw):
            seen.append((list(texts), context))
            return [t.upper() for t in texts]

    monkeypatch.setattr(pipeline, "_pipeline", FakeEnsemble())
    assert translate_mcm.translate_batch(["hello"], "ctx") == ["HELLO"]
    assert seen == [(["hello"], "ctx")]
    assert callable(pipeline.get_mod_context)


# ── 3–5. SWF ──────────────────────────────────────────────────────────────────

def _ffdec_import_fake(written):
    """subprocess.run для SwfStringCache: -importText пишет переведённый SWF."""
    def fake_run(cmd, **kw):
        if "-importText" in cmd:
            _swf, out_swf, import_dir = Path(cmd[4]), Path(cmd[5]), Path(cmd[6])
            texts = {p.stem: p.read_text(encoding="utf-8") for p in import_dir.glob("*.txt")}
            written.update(texts)
            out_swf.write_bytes(b"TRANSLATED-SWF")
        return NS(returncode=0, stdout="", stderr=b"")
    return fake_run


def test_loose_swf_only_mod_imports_db_translations_via_paths_ffdec(tmp_path, repo,
                                                                     no_generation,
                                                                     monkeypatch):
    """Мод только с loose SWF доходит до экспорта; FFDec берётся из paths.ffdec_jar;
    переведённый файл встаёт на место оригинала; модель не вызывается."""
    from translator.web import asset_cache
    from translator.pipeline import apply_pipeline

    mod = tmp_path / "mods" / MOD
    swf = mod / "interface" / "audit.swf"
    swf.parent.mkdir(parents=True)
    swf.write_bytes(b"ORIGINAL-SWF")
    jar = tmp_path / "ffdec-cli.jar"
    jar.write_bytes(b"")
    cfg = _cfg(tmp_path, ffdec_jar=jar)

    cache = asset_cache.SwfStringCache(cfg.paths.temp_dir, str(jar))
    cd = cache._cache_dir(MOD, "interface/audit.swf")
    cd.mkdir(parents=True)
    (cd / "7_en.txt").write_text("Hello traveller", encoding="utf-8")
    repo.upsert(MOD, "audit.swf", "swf:interface/audit.swf:7",
                "Hello traveller", "Привет, путник", "translated", 100)

    legacy = MagicMock()
    monkeypatch.setattr(apply_pipeline, "_translate_swf_texts", legacy)
    written = {}
    monkeypatch.setattr(asset_cache.subprocess, "run", _ffdec_import_fake(written))

    _run(cfg, repo)

    assert written == {"7": "Привет, путник"}
    assert swf.read_bytes() == b"TRANSLATED-SWF"
    assert not (swf.parent / "_translated_audit.swf").exists()
    assert (cfg.paths.backup_dir / MOD / "interface" / "audit.swf").read_bytes() == b"ORIGINAL-SWF"
    legacy.assert_not_called()


def test_swf_without_db_rows_falls_back_with_configured_ffdec(tmp_path, repo, monkeypatch):
    """Без swf:-строк в базе остаётся старый FFDec-путь — и он видит paths.ffdec_jar."""
    from translator.pipeline import apply_pipeline

    mod = tmp_path / "mods" / MOD
    (mod / "audit.swf").parent.mkdir(parents=True)
    (mod / "audit.swf").write_bytes(b"x")
    jar = tmp_path / "ffdec-cli.jar"
    jar.write_bytes(b"")
    legacy = MagicMock()
    monkeypatch.setattr(apply_pipeline, "_translate_swf_texts", legacy)

    _run(_cfg(tmp_path, ffdec_jar=jar), repo)

    legacy.assert_called_once()
    assert legacy.call_args.args[2] == jar


def test_legacy_swf_helper_puts_translated_file_on_original_path(tmp_path, monkeypatch):
    from translator.pipeline.apply_pipeline import _translate_swf_texts
    from translator.parsing import swf_handler
    from scripts import esp_engine
    import translator.pipeline as pipeline

    mod = tmp_path / "mods" / MOD
    mod.mkdir(parents=True)
    swf = mod / "audit.swf"
    swf.write_bytes(b"ORIGINAL-SWF")
    cfg = _cfg(tmp_path)
    imported = {}

    def export(jar, source, texts_dir):
        (Path(texts_dir) / "1.txt").write_text("0 | Hello traveller", encoding="utf-8")

    def import_(jar, source, texts_dir, dest):
        imported["text"] = (Path(texts_dir) / "1.txt").read_text(encoding="utf-8")
        Path(dest).write_bytes(b"TRANSLATED-SWF")

    monkeypatch.setattr(swf_handler, "export_texts", export)
    monkeypatch.setattr(swf_handler, "import_texts", import_)
    monkeypatch.setattr(pipeline, "get_mod_context", lambda _: "")
    monkeypatch.setattr(esp_engine, "translate_texts", lambda *a, **k: [
        {"translation": "Привет, путник", "skipped": False, "token_issues": []}])

    _translate_swf_texts(_job(), swf, "fake.jar", cfg)

    assert imported["text"] == "0 | Привет, путник"
    assert swf.read_bytes() == b"TRANSLATED-SWF"
    assert not (mod / "_translated_audit.swf").exists()
    assert not (mod / "_swftexts_audit").exists()
    assert (cfg.paths.backup_dir / MOD / "audit.swf").read_bytes() == b"ORIGINAL-SWF"


def test_empty_mod_still_reports_nothing_to_translate(tmp_path, repo):
    (tmp_path / "mods" / MOD).mkdir(parents=True)
    job = _run(_cfg(tmp_path), repo)
    assert any("No BSA archives" in line for line in job.log_lines)
