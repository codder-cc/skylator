"""Разметить доставленные правки имён: годная (judge='termfix') или нет.

Пакет правки имён (задания прохода согласованности, translator/validation/
inline_names.py) идёт без судьи — судить там нечего, задача узкая. Поэтому годность
проверяется здесь, детерминированно: каждое требуемое имя теперь записано так, как
требовалось. Годная правка получает judge='termfix', и политика применения
(`promote.decide`) дальше проверяет её как обычно: правила, род, ломаная речь, имена,
и что правка тронула не больше трети слов. Негодная — judge='termfix_failed', в
корпус не пойдёт.

Запускать можно сколько угодно раз: по мере доставки размечаются новые строки.

    python scripts/tag_termfix.py logs/termfix_inline_names_20260927.json
"""
from __future__ import annotations

import io
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from translator.validation import inline_names as IN  # noqa: E402

out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def fixed(requirement: str, translation: str) -> bool:
    for part in requirement.split("; "):
        if " = " not in part:
            continue
        en, want = part.split(" = ", 1)
        tok = IN.rendering_of(en, translation)
        if not (tok and IN.same(tok, want)):
            return False
    return True


def main() -> None:
    meta = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    tasks, job = meta["tasks"], meta["job_id"]
    con = sqlite3.connect(str(ROOT / "cache" / "translations.db"), timeout=120)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=120000")
    rows = con.execute("SELECT id, string_id, translation FROM candidates WHERE job_id=? "
                       "AND judge IS NULL", (job,)).fetchall()
    good = bad = 0
    for r in rows:
        req = tasks.get(str(r["string_id"])) or ""
        ok = bool(req) and fixed(req, r["translation"] or "")
        con.execute("UPDATE candidates SET judge=? WHERE id=?",
                    ("termfix" if ok else "termfix_failed", r["id"]))
        good += ok
        bad += not ok
    con.commit()
    total = con.execute("SELECT judge, COUNT(*) FROM candidates WHERE job_id=? GROUP BY 1",
                        (job,)).fetchall()
    print(f"размечено сейчас: годных {good}, негодных {bad}; всего по заданию: "
          f"{dict(total)}", file=out)


if __name__ == "__main__":
    main()
