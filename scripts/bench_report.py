"""Сводка стенда при t=0: каждый опыт против базы t=0 своего набора.

При t=0 генерация повторяема (t00_D и t00b_D совпали в 150 из 150 строк), поэтому
весь разброс — от выборки строк, и парный бутстреп по строкам (для S — по темам) —
честный тест. Помечается «значимо», только если 95% интервал не содержит ноль;
сравнений много, так что одиночная «значимость» на грани — повод повторить, а не
вывод.
"""
import io
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import bench as B  # noqa: E402

out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
BASE = {"D": "t00_D", "H": "t00_H", "S": "line_t0_S"}


def main() -> None:
    done = set(json.loads((ROOT / "logs" / "bench_done.json").read_text()))
    for f in sorted((ROOT / "logs" / "experiments").glob("*.json")):
        m = json.loads(f.read_text(encoding="utf-8"))
        n, s = m["name"], m["set"]
        if n.startswith("_") or n not in done or n == BASE[s] or m.get("limit"):
            continue
        if not (n.startswith("t0_") or n.startswith("scene_t0") or n == "t00b_D"):
            continue
        if BASE[s] not in done:
            continue
        B.score(n, quiet=True)
        sc = json.loads(f.read_text(encoding="utf-8"))["score"]
        extra = (f"имена {sc.get('name_ok')} род {sc.get('gender_ok')} брак {sc['defect']}"
                 if s == "D" else f"чисто {sc.get('clean')}% брак {sc['defect']}" if s == "H"
                 else f"разнобой имён {sc.get('names_mixed')} брак {sc['defect']}")
        print(f"\n{n:<22} {extra}", file=out, flush=True)
        out.flush()
        subprocess.run([sys.executable, str(ROOT / "scripts" / "bench.py"), "compare", BASE[s], n])


if __name__ == "__main__":
    main()
