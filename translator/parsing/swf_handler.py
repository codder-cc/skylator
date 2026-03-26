"""
translator.parsing.swf_handler — FFDec (JPEXS Free Flash Decompiler) subprocess wrappers.

Public API:
  export_texts(ffdec_jar, swf_path, out_dir) → None
  import_texts(ffdec_jar, swf_path, texts_dir, out_swf) → None
  decompile(ffdec_jar, swf_path, out_dir) → None
  compile_texts(ffdec_jar, swf_path, src_dir, out_swf) → None
"""
from __future__ import annotations
import subprocess
from pathlib import Path


def export_texts(
    ffdec_jar: str | Path,
    swf_path: str | Path,
    out_dir: str | Path,
    timeout: int = 120,
) -> None:
    """Export text strings from a SWF file to a directory.

    Raises RuntimeError on non-zero exit.
    """
    result = subprocess.run(
        ["java", "-jar", str(ffdec_jar), "-export", "texts", str(out_dir), str(swf_path)],
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"FFDec export texts failed: {result.stderr[:500]}")


def import_texts(
    ffdec_jar: str | Path,
    swf_path: str | Path,
    texts_dir: str | Path,
    out_swf: str | Path,
    timeout: int = 120,
) -> None:
    """Import translated text files back into a SWF.

    Raises RuntimeError on non-zero exit.
    """
    result = subprocess.run(
        ["java", "-jar", str(ffdec_jar), "-importtexts",
         str(swf_path), str(texts_dir), str(out_swf)],
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"FFDec import texts failed: {result.stderr[:500]}")


def decompile(
    ffdec_jar: str | Path,
    swf_path: str | Path,
    out_dir: str | Path,
    timeout: int = 120,
) -> None:
    """Full decompile (all assets) of a SWF.

    Raises RuntimeError on non-zero exit.
    """
    result = subprocess.run(
        ["java", "-jar", str(ffdec_jar), "-export", "all", str(out_dir), str(swf_path)],
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr[:500])


def compile_texts(
    ffdec_jar: str | Path,
    swf_path: str | Path,
    src_dir: str | Path,
    out_swf: str | Path,
    timeout: int = 120,
) -> None:
    """Recompile a SWF from a decompiled scripts directory.

    Raises RuntimeError on non-zero exit.
    """
    result = subprocess.run(
        ["java", "-jar", str(ffdec_jar), "-importScript",
         str(swf_path), str(swf_path), str(src_dir)],
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr[:500])
