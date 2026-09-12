"""
A plugin's name is also a directory name, and the apply glob did not know it.

Skyrim keeps a plugin's voice lines in Sound\\Voice\\<Plugin>.esp\\ and its facegen data
in textures\\...\\FaceTint\\<Plugin>.esp\\ — both directories, both named after the
plugin, both matched by rglob("*.esp"). The apply opened them to write and got

    [Errno 13] Permission denied: '...\\Sound\\Voice\\DBM_Fossils_Patch.esp'

on 36 mods, which reads like a locked file and is nothing of the kind. Every one of
those mods silently kept its English.
"""
import inspect

from translator.pipeline.apply_pipeline import ApplyPipeline


def test_the_apply_takes_only_files(tmp_path):
    """The shape the bug had: a real plugin beside a directory of the same name."""
    mod = tmp_path / "SomeMod"
    (mod / "Sound" / "Voice" / "Listen.esp").mkdir(parents=True)
    (mod / "textures" / "FaceTint" / "Listen.esp").mkdir(parents=True)
    (mod / "Listen.esp").write_bytes(b"TES4")
    (mod / "Other.esm").write_bytes(b"TES4")

    found = [p for p in list(mod.rglob("*.esp")) + list(mod.rglob("*.esm")) if p.is_file()]
    assert sorted(p.name for p in found) == ["Listen.esp", "Other.esm"]
    assert len(list(mod.rglob("*.esp"))) == 3, "the unfiltered glob really does match both"


def test_the_filter_is_in_the_apply_path():
    src = inspect.getsource(ApplyPipeline.run_esp)
    assert "is_file()" in src
    i = src.index("rglob")
    assert "is_file()" in src[max(0, i - 200):i + 200], "on the glob, not somewhere else"


def test_the_cli_walks_the_same_tree():
    import translator.cli as cli
    src = inspect.getsource(cli)
    i = src.index('rglob("*.esp")')
    assert "is_file()" in src[i - 120:i + 120]
