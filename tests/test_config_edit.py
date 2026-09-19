"""Saving a setting must not cost the user their comments.

config.yaml is a file people edit and annotate. Round-tripping it through
yaml.safe_load/safe_dump saves the value and deletes the notes in the same breath --
measured on a real config, where two explanatory lines above `hf_token` vanished on the
first save. These tests pin the line-level editor that replaced it.
"""
from __future__ import annotations

import pytest

from translator.config_edit import ConfigEditError, set_value, set_values

SAMPLE = '''\
# ── Models ──────────────────────────────────────────
models:
  # HuggingFace token for gated downloads. Never logged.
  hf_token: "hf_example"

nexus:
  api_key: "SECRET"          # from the account page
  game: "skyrimspecialedition"
  # Where finished archives land.
  download_dir: "cache/downloads"
  max_concurrent: 3
  browser_enabled: true

ensemble:
  adaptive_threshold: 999999
'''


@pytest.fixture
def cfg(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(SAMPLE, encoding="utf-8")
    return p


def test_changing_a_value_keeps_every_comment(cfg):
    before = cfg.read_text(encoding="utf-8")
    set_value(cfg, "nexus", "download_dir", "H:/Nolvus/Instances/ARCHIVE")
    after = cfg.read_text(encoding="utf-8")

    assert after.count("#") == before.count("#")
    assert "# HuggingFace token for gated downloads. Never logged." in after
    assert "# Where finished archives land." in after
    assert 'download_dir: "H:/Nolvus/Instances/ARCHIVE"' in after


def test_nothing_outside_the_edited_line_moves(cfg):
    before = cfg.read_text(encoding="utf-8").splitlines()
    set_value(cfg, "nexus", "max_concurrent", 5)
    after = cfg.read_text(encoding="utf-8").splitlines()

    assert len(before) == len(after)
    differing = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    assert len(differing) == 1
    assert after[differing[0]].strip() == "max_concurrent: 5"


def test_an_inline_comment_survives_its_value_changing(cfg):
    set_value(cfg, "nexus", "api_key", "NEWSECRET")
    line = next(l for l in cfg.read_text(encoding="utf-8").splitlines()
                if "api_key" in l)
    assert "NEWSECRET" in line and "# from the account page" in line


def test_a_missing_key_is_inserted_into_its_own_block(cfg):
    set_value(cfg, "nexus", "donor_dir", "cache/donors")
    lines = cfg.read_text(encoding="utf-8").splitlines()
    i_nexus = lines.index("nexus:")
    i_ens   = lines.index("ensemble:")
    i_new   = next(i for i, l in enumerate(lines) if l.strip().startswith("donor_dir:"))
    # Inside the nexus block, not appended to the end of the file.
    assert i_nexus < i_new < i_ens
    assert lines[i_new] == "  donor_dir: cache/donors"


def test_booleans_and_numbers_are_written_as_yaml_not_as_python(cfg):
    set_values(cfg, "nexus", {"browser_enabled": False, "max_concurrent": 8})
    text = cfg.read_text(encoding="utf-8")
    assert "browser_enabled: false" in text and "False" not in text
    assert "max_concurrent: 8" in text


def test_a_windows_path_is_quoted_so_it_survives_a_reload(cfg, tmp_path):
    # A bare backslash path is a YAML escape hazard; quoting it is what makes the value
    # come back the way it went in.
    import yaml

    set_value(cfg, "nexus", "download_dir", r"H:\Nolvus\Instances\ARCHIVE")
    loaded = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert loaded["nexus"]["download_dir"] == r"H:\Nolvus\Instances\ARCHIVE"


def test_the_whole_document_still_parses_after_editing(cfg):
    import yaml

    set_values(cfg, "nexus", {"download_dir": "D:/mods", "max_concurrent": 2,
                              "archive_tool_path": "C:/Program Files/7-Zip/7z.exe"})
    d = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert d["nexus"]["download_dir"] == "D:/mods"
    assert d["nexus"]["max_concurrent"] == 2
    assert d["nexus"]["archive_tool_path"] == "C:/Program Files/7-Zip/7z.exe"
    # Untouched blocks are untouched.
    assert d["models"]["hf_token"] == "hf_example"
    assert d["ensemble"]["adaptive_threshold"] == 999999


def test_an_absent_block_is_refused_rather_than_appended(cfg):
    with pytest.raises(ConfigEditError):
        set_value(cfg, "nowhere", "x", 1)


def test_set_values_reports_only_what_actually_changed(cfg):
    changed = set_values(cfg, "nexus", {"max_concurrent": 3, "game": "skyrim"})
    assert changed == ["game"]          # max_concurrent was already 3
