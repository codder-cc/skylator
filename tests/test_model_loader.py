"""
Model path resolution — where a .gguf actually comes from.

98 statements at 10% coverage. A mistake here means the wrong model is loaded, or
a multi-shard model is only half fetched and llama.cpp fails on a file that looks
present. Neither is obvious from the outside.

Resolution order: absolute path as given → model_cache_dir/<dir>/<file> → download.
"""
from pathlib import Path

import pytest

from translator.models import loader


def _gguf(p: Path, name: str) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    f = p / name
    f.write_bytes(b"GGUF")
    return f


def test_absolute_path_is_used_as_given(tmp_path):
    f = _gguf(tmp_path / "models", "m.gguf")
    got = loader.resolve_gguf("org/repo", str(tmp_path / "models"), "m.gguf")
    assert Path(got) == f


def test_relative_dir_resolves_under_the_model_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "_get_model_cache_dir", lambda: tmp_path / "cache")
    f = _gguf(tmp_path / "cache" / "Qwen", "m.gguf")
    assert Path(loader.resolve_gguf("org/repo", "Qwen", "m.gguf")) == f


def test_existing_file_is_never_downloaded(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "_get_model_cache_dir", lambda: tmp_path / "cache")
    _gguf(tmp_path / "cache" / "Qwen", "m.gguf")

    def _boom(**kw):
        raise AssertionError("download attempted for a file already on disk")
    monkeypatch.setattr("huggingface_hub.hf_hub_download", _boom)

    loader.resolve_gguf("org/repo", "Qwen", "m.gguf")


def test_single_file_model_downloads_exactly_one_file(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "_get_model_cache_dir", lambda: tmp_path / "cache")
    asked = []

    def _fake(repo_id, filename, local_dir, **kw):
        asked.append(filename)
        _gguf(Path(local_dir), filename)
        return str(Path(local_dir) / filename)
    monkeypatch.setattr("huggingface_hub.hf_hub_download", _fake)

    loader.resolve_gguf("org/repo", "Qwen", "m.gguf")
    assert asked == ["m.gguf"]


def test_multi_shard_model_downloads_every_shard(tmp_path, monkeypatch):
    """A half-fetched split model looks present but fails to load."""
    monkeypatch.setattr(loader, "_get_model_cache_dir", lambda: tmp_path / "cache")
    asked = []

    def _fake(repo_id, filename, local_dir, **kw):
        asked.append(filename)
        _gguf(Path(local_dir), filename)
        return str(Path(local_dir) / filename)
    monkeypatch.setattr("huggingface_hub.hf_hub_download", _fake)

    got = loader.resolve_gguf("org/repo", "Qwen", "big-00001-of-00003.gguf")
    assert asked == ["big-00001-of-00003.gguf",
                     "big-00002-of-00003.gguf",
                     "big-00003-of-00003.gguf"]
    assert got.endswith("big-00001-of-00003.gguf")


def test_pointing_at_a_later_shard_still_fetches_the_whole_set(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "_get_model_cache_dir", lambda: tmp_path / "cache")
    asked = []
    monkeypatch.setattr("huggingface_hub.hf_hub_download",
                        lambda repo_id, filename, local_dir, **kw: (
                            asked.append(filename), _gguf(Path(local_dir), filename))[1])

    loader.resolve_gguf("org/repo", "Qwen", "big-00002-of-00003.gguf")
    assert len(asked) == 3


def test_a_download_failure_names_the_model(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "_get_model_cache_dir", lambda: tmp_path / "cache")
    monkeypatch.setattr("huggingface_hub.hf_hub_download",
                        lambda **kw: (_ for _ in ()).throw(OSError("404")))
    with pytest.raises(RuntimeError) as e:
        loader.resolve_gguf("org/repo", "Qwen", "m.gguf")
    assert "m.gguf" in str(e.value) and "org/repo" in str(e.value)


def test_model_dir_detection(tmp_path):
    assert loader._has_model_files(tmp_path) is False
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    assert loader._has_model_files(tmp_path) is True

    other = tmp_path / "safet"
    other.mkdir()
    (other / "model-00001.safetensors").write_bytes(b"x")
    assert loader._has_model_files(other) is True


def test_default_cache_dir_stays_inside_the_project(tmp_path):
    """Models must never land in a system cache — they are tens of gigabytes."""
    d = loader.default_model_cache_dir()
    assert d.name == "models"
    assert "translator" not in d.parts[-1:]
