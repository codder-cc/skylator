"""
translator.parsing.mcm_handler — MCM translation .txt file read/write.

MCM files are UTF-16-LE tab-delimited key\tvalue pairs (BOM optional).

Public API:
  read(path) → (pairs, bom)
  write(path, pairs, bom)
"""
from __future__ import annotations
from pathlib import Path


def read(path: str | Path) -> tuple[list[tuple[str, str]], bytes]:
    """Read an MCM translation .txt file.

    Returns (pairs, bom) where:
      pairs — list of (key, value) tuples
      bom   — raw BOM bytes (b'' if not present, typically b'\\xff\\xfe')
    """
    from scripts.translate_mcm import read_trans_file
    return read_trans_file(Path(path))


def write(path: str | Path, pairs: list[tuple[str, str]], bom: bytes = b"\xff\xfe") -> None:
    """Write MCM translation pairs to a UTF-16-LE .txt file.

    Args:
        path:  destination path
        pairs: list of (key, value) tuples
        bom:   byte-order mark (default: UTF-16-LE BOM)
    """
    lines = [f"{k}\t{v}" if v else k for k, v in pairs]
    content = "\r\n".join(lines) + "\r\n"
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(bom + content.encode("utf-16-le"))
