"""Пересчёт результатов mt_bench с одинаковой нормализацией формата для всех моделей.

Лишняя конечная точка там, где её нет в исходнике, и варианты списком вместо одного
ответа — это подключение модели, а не качество перевода. Нормализация одна для всех,
включая боевой профиль (cand_H / cand_D со стенда M5).

    python scripts/mt_score.py
"""
import io, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import bench as B
from translator.validation.quality import compute_string_status


def norm(src: str, t: str) -> str:
    t = (t or "").strip()
    if "\n" not in (src or "") and "\n" in t and len(src or "") <= 40:
        t = t.split("\n", 1)[0].strip()
    if (src or "").rstrip()[-1:] not in ".!?…" and t.endswith(".") and not t.endswith(".."):
        t = t[:-1]
    return t


def score(set_name, ans):
    items = B._load_set(set_name)
    fixed = {}
    for it in items:
        a = ans.get(str(it["id"])) or ans.get(it["id"]) or ans.get(int(it["id"]))
        t = norm(it["en"], (a or {}).get("translation", ""))
        st = compute_string_status(it["en"], t, None, it.get("rec_type"), it.get("field_type"))[3]
        fixed[it["id"]] = {"translation": t, "rules_status": st, "finish_reason": "stop"}
    per = list((B.score_H(items, fixed) if set_name == "H" else B.score_D(items, fixed))["per"].values())
    if set_name == "H":
        return {"clean%": round(100 * sum(p["clean"] for p in per) / len(per), 1),
                "defect": sum(p["defect"] for p in per)}
    g = [p["gender_ok"] for p in per if p["gender_ok"] is not None]
    nm = [p["name_ok"] for p in per if p["name_ok"] is not None]
    return {"chrF": round(sum(p["chrf"] for p in per) / len(per), 2), "names": f"{sum(nm)}/{len(nm)}",
            "gender": f"{sum(g)}/{len(g)}", "defect": sum(p["defect"] for p in per)}


rows = []
for s in ("H", "D"):
    m = json.loads((ROOT / "logs" / "experiments" / f"cand_{s}.json").read_text(encoding="utf-8"))
    a = {str(k): dict(v) for k, v in B._answers(m["job_id"]).items()}
    rows.append((s, "qwen27 боевой профиль (M5)", score(s, a)))
    for f in sorted((ROOT / "logs" / "experiments").glob(f"mt_*_{s}.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        if d["summary"]["n"] < (616 if s == "H" else 150):
            continue
        rows.append((s, d["name"][3:-2], score(s, {str(k): v for k, v in d["answers"].items()})
                     | {"s/line": d["summary"]["sec_per_line"]}))
for r in rows:
    print(f"{r[0]}  {r[1]:<28} {r[2]}", file=out)
