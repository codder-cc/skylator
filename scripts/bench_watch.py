"""Ждать, пока закончится хотя бы один новый опыт стенда, и назвать его.

Опыт закончен, когда в слое есть ответ на каждую строку его набора. Уже замеченные
хранятся в logs/bench_done.json, чтобы каждый опыт объявлялся один раз.
"""
import io
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
DONE = ROOT / "logs" / "bench_done.json"


def finished() -> set:
    con = sqlite3.connect(str(ROOT / "cache" / "translations.db"), timeout=60)
    sys.path.insert(0, str(ROOT / "scripts"))
    from bench import _load_set
    sizes = {s: len(_load_set(s)) for s in ("H", "D", "S")}
    got = set()
    for f in (ROOT / "logs" / "experiments").glob("*.json"):
        m = json.loads(f.read_text(encoding="utf-8"))
        n = con.execute("SELECT COUNT(DISTINCT string_id) FROM candidates WHERE job_id=?",
                        (m["job_id"],)).fetchone()[0]
        if n >= sizes[m["set"]]:
            got.add(m["name"])
    return got


def main() -> None:
    seen = set(json.loads(DONE.read_text())) if DONE.exists() else set()
    while True:
        new = finished() - seen
        if new:
            DONE.write_text(json.dumps(sorted(seen | new)))
            print("готово: " + ", ".join(sorted(new)), file=out, flush=True)
            return
        time.sleep(120)


if __name__ == "__main__":
    main()
