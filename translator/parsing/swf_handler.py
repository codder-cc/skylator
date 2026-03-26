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


def list_fonts(
    ffdec_jar: str | Path,
    swf_path: str | Path,
    timeout: int = 60,
) -> list[dict]:
    """Return a list of fonts embedded in a SWF.

    Each dict: {id: int, name: str, style: str}
    Raises RuntimeError on non-zero exit.
    """
    result = subprocess.run(
        ["java", "-jar", str(ffdec_jar), "-listfonts", str(swf_path)],
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"FFDec listfonts failed: {result.stderr[:500]}")

    fonts = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        # Expected format: "<id> <name> <style>" e.g. "1 $SkyrimEasyFont Regular"
        parts = line.split(None, 2)
        if parts:
            fonts.append({
                "id":    parts[0],
                "name":  parts[1] if len(parts) > 1 else "",
                "style": parts[2] if len(parts) > 2 else "",
            })
    return fonts


def replace_font(
    ffdec_jar: str | Path,
    swf_path: str | Path,
    out_swf: str | Path,
    font_id: str | int,
    ttf_path: str | Path,
    timeout: int = 120,
) -> None:
    """Replace a font in a SWF by its ID with a TTF file.

    Raises RuntimeError on non-zero exit.
    """
    result = subprocess.run(
        ["java", "-jar", str(ffdec_jar),
         "-replaceFont", str(swf_path), str(font_id), str(ttf_path), str(out_swf)],
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"FFDec replaceFont failed: {result.stderr[:500]}")


def replace_font_by_name(
    ffdec_jar: str | Path,
    swf_path: str | Path,
    out_swf: str | Path,
    font_name: str,
    ttf_path: str | Path,
    timeout: int = 120,
) -> None:
    """Replace a font in a SWF by its name with a TTF file.

    Raises RuntimeError on non-zero exit.
    """
    result = subprocess.run(
        ["java", "-jar", str(ffdec_jar),
         "-replaceFontByName", str(swf_path), font_name, str(ttf_path), str(out_swf)],
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"FFDec replaceFontByName failed: {result.stderr[:500]}")
