"""
Backup restore — the only code path that deletes a live mod folder.

245 statements at 15% coverage on a route that overwrites the user's game files.
The restore sequence is: copy the current folder aside, delete it, then copy the
backup into its place. If the last step fails the mod is simply gone, so the
failure path matters more than the happy one.
"""
import shutil
from pathlib import Path

import pytest
from flask import Flask

from translator.web.routes.backups import bp


class _Paths:
    def __init__(self, root: Path):
        self.backup_dir = root / "backups"
        self.mods_dir = root / "mods"
        self.mods_dirs = [self.mods_dir]
        self.translation_cache = root / "cache" / "tc.json"


class _Cfg:
    def __init__(self, root): self.paths = _Paths(root)


@pytest.fixture
def app(tmp_path):
    a = Flask(__name__)
    a.register_blueprint(bp)
    cfg = _Cfg(tmp_path)
    cfg.paths.backup_dir.mkdir(parents=True)
    cfg.paths.mods_dir.mkdir(parents=True)
    (tmp_path / "cache").mkdir()
    a.config["TRANSLATOR_CFG"] = cfg
    a.config["SCANNER"] = None
    return a, cfg


def _make_backup(cfg, mod="Mod", label="manual", content="backed up"):
    b = cfg.paths.backup_dir / f"{mod}__20260101_000000__{label}"
    b.mkdir(parents=True)
    (b / "Mod.esp").write_text(content, encoding="utf-8")
    return b.name


def _make_live(cfg, mod="Mod", content="live"):
    d = cfg.paths.mods_dir / mod
    d.mkdir(parents=True, exist_ok=True)
    (d / "Mod.esp").write_text(content, encoding="utf-8")
    return d


def test_restore_replaces_the_live_folder(app):
    a, cfg = app
    bid = _make_backup(cfg)
    live = _make_live(cfg)

    r = a.test_client().post(f"/backups/{bid}/restore")
    assert r.status_code == 200
    assert (live / "Mod.esp").read_text(encoding="utf-8") == "backed up"


def test_restore_keeps_the_previous_state_aside(app):
    a, cfg = app
    bid = _make_backup(cfg)
    _make_live(cfg)

    a.test_client().post(f"/backups/{bid}/restore")
    safety = list(cfg.paths.backup_dir.glob("Mod__before_restore__*"))
    assert safety, "the replaced folder must be kept"
    assert (safety[0] / "Mod.esp").read_text(encoding="utf-8") == "live"


def test_restore_into_a_missing_folder_works(app):
    a, cfg = app
    bid = _make_backup(cfg)
    r = a.test_client().post(f"/backups/{bid}/restore")
    assert r.status_code == 200
    assert (cfg.paths.mods_dir / "Mod" / "Mod.esp").is_file()


def test_a_failed_restore_does_not_leave_the_mod_missing(app, monkeypatch):
    """The sequence deletes the live folder before writing the backup into place.
    If that write fails, the mod must be put back, not left deleted."""
    a, cfg = app
    bid = _make_backup(cfg)
    live = _make_live(cfg)

    real_copytree = shutil.copytree
    calls = {"n": 0}

    def flaky(src, dst, *args, **kw):
        calls["n"] += 1
        if calls["n"] == 2:            # 1 = safety copy, 2 = the restore itself
            raise OSError("disk full")
        return real_copytree(src, dst, *args, **kw)

    monkeypatch.setattr("translator.web.routes.backups.shutil.copytree", flaky)

    a.test_client().post(f"/backups/{bid}/restore")

    assert live.is_dir(), "the live mod folder was deleted and never restored"
    assert (live / "Mod.esp").read_text(encoding="utf-8") == "live"


def test_unknown_backup_is_404(app):
    a, _ = app
    assert a.test_client().post("/backups/Nope__1__x/restore").status_code == 404


def test_backup_id_without_a_mod_name_is_rejected(app):
    a, cfg = app
    (cfg.paths.backup_dir / "weird").mkdir()
    assert a.test_client().post("/backups/weird/restore").status_code == 400


def test_delete_removes_the_backup(app):
    a, cfg = app
    bid = _make_backup(cfg)
    assert a.test_client().post(f"/backups/{bid}/delete").status_code == 200
    assert not (cfg.paths.backup_dir / bid).exists()


def test_restore_without_a_configured_backup_dir_is_a_clean_error(app):
    """paths.backup_dir is Optional; an unset one must not surface as a 500 traceback."""
    a, cfg = app
    cfg.paths.backup_dir = None
    r = a.test_client().post("/backups/Mod__1__x/restore")
    assert r.status_code == 400
    assert "backup_dir" in (r.get_json() or {}).get("error", "")
