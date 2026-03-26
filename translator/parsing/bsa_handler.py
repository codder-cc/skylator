"""
translator.parsing.bsa_handler — BSArch subprocess wrappers.

Public API:
  unpack(bsarch_exe, bsa_path, out_dir) → None
  pack(bsarch_exe, src_dir, bsa_path) → None
"""
from __future__ import annotations
import subprocess
from pathlib import Path


def unpack(bsarch_exe: str | Path, bsa_path: str | Path, out_dir: str | Path) -> None:
    """Unpack a BSA archive using BSArch.

    Raises RuntimeError on non-zero exit.
    """
    result = subprocess.run(
        [str(bsarch_exe), "unpack", str(bsa_path), str(out_dir), "-q", "-mt"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"BSArch unpack failed: {result.stderr[:500]}")


def pack(bsarch_exe: str | Path, src_dir: str | Path, bsa_path: str | Path) -> None:
    """Pack a directory into a BSA archive using BSArch (SSE format).

    Raises RuntimeError on non-zero exit.
    """
    result = subprocess.run(
        [str(bsarch_exe), "pack", str(src_dir), str(bsa_path), "-sse", "-mt"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"BSArch pack failed: {result.stderr[:500]}")
