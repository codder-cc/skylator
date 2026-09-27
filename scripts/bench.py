"""Испытательный стенд: опыт боевым путём, мера, парное сравнение.

Аудит 27.09 показал, что выводы «контекст не помогает», «судья не умеет» могли
описывать не метод, а сломанное подключение: ручной /infer не проверяет сборку
пакета, а по пути терялись промпт, параметры и контекст. Поэтому здесь опыт — это
настоящее задание review_strings на машину-стенд: пакет → агент → MLX → слой
кандидатов, с полной трассой каждого вызова. Корпус не меняется (режим «только в
слой»).

НАБОРЫ (фиксированы один раз, logs/)

    H   eval_set_H.json   строки корпуса с известным официальным ответом (эталон),
                          по типам записей. Меры: точно / без огрехов / по леммам.
    D   eval_set_D.json   150 реплик модов; эталон — eval_set_D_ref.json (один верный
                          вариант из многих). Меры: chrF к эталону, род говорящего,
                          игровые имена, брак правил, доля инфинитивов у ломаной речи.

КОМАНДЫ

    python scripts/bench.py run  --name t03 --set H --params '{"temperature":0.3}'
    python scripts/bench.py run  --name ctx_none --set D --context-parts ""
    python scripts/bench.py score t03
    python scripts/bench.py compare t00 t03

Сравнение всегда парное — по одним и тем же строкам. Для H: расхождения «верно
только в A / только в B» и знаковый тест. Для D: средняя разность chrF по строкам и
её 95% интервал бутстрепом.
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import math
import os
import random
import re
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
BASE = "http://127.0.0.1:5000"
BENCH = "darwin-int00mac-7PKF2W"
EXP_DIR = ROOT / "logs" / "experiments"
DB = ROOT / "cache" / "translations.db"


def _post(url, payload, t=1800):
    req = urllib.request.Request(BASE + url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Accept": "application/json"}, method="POST")
    return json.load(urllib.request.urlopen(req, timeout=t))


def _load_set(name: str) -> list:
    return json.loads((ROOT / "logs" / f"eval_set_{name}.json").read_text(encoding="utf-8"))


# ── меры ────────────────────────────────────────────────────────────────────

def chrf(hyp: str, ref: str, n: int = 6, beta: float = 2.0) -> float:
    """chrF (символьные n-граммы 1..6, β=2), 0..100. Пробелы не учитываются."""
    h, r = re.sub(r"\s+", "", hyp or ""), re.sub(r"\s+", "", ref or "")
    if not h or not r:
        return 0.0
    precs, recs = [], []
    for k in range(1, n + 1):
        hg = collections.Counter(h[i:i + k] for i in range(len(h) - k + 1))
        rg = collections.Counter(r[i:i + k] for i in range(len(r) - k + 1))
        if not hg or not rg:
            continue
        m = sum((hg & rg).values())
        precs.append(m / sum(hg.values()))
        recs.append(m / sum(rg.values()))
    if not precs:
        return 0.0
    p, rc = sum(precs) / len(precs), sum(recs) / len(recs)
    if p + rc == 0:
        return 0.0
    return 100 * (1 + beta ** 2) * p * rc / (beta ** 2 * p + rc)


def _answers(job_id: str) -> dict:
    con = sqlite3.connect(str(DB), timeout=60)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM candidates WHERE job_id=? ORDER BY produced_at",
                       (job_id,)).fetchall()
    return {r["string_id"]: r for r in rows}           # последний ответ на строку


def score_H(items, ans) -> dict:
    os.environ["SKYLATOR_APPLY_HOLDOUT"] = "1"
    from translator.validation.authority import cosmetic_key
    sys.path.insert(0, str(ROOT / "scripts"))
    from context_holdout import by_lemma, loose
    per, by_type = {}, collections.defaultdict(collections.Counter)
    for it in items:
        a = ans.get(it["id"])
        t = (a["translation"] if a else "") or ""
        exact = t.strip() == it["off"].strip()
        clean = exact or cosmetic_key(t) == cosmetic_key(it["off"]) or loose(t) == loose(it["off"])
        lem = bool(t) and by_lemma(it["off"], t)
        per[it["id"]] = {"answered": bool(a), "exact": exact, "clean": clean, "lemma": lem,
                         "defect": bool(a and a["rules_status"] != "translated"),
                         "cut": bool(a and (a["finish_reason"] or "") == "length")}
        c = by_type[it["rec_type"]]
        c["n"] += 1
        c["clean"] += clean
    return {"per": per, "by_type": {k: dict(v) for k, v in by_type.items()}}


def score_D(items, ans) -> dict:
    from translator.db.promote import _names, first_person_gender, looks_pidgin
    from translator.validation.official_context import _mid_sentence_caps
    refs = json.loads((ROOT / "logs" / "eval_set_D_ref.json").read_text(encoding="utf-8"))["refs"]
    names = _names()
    per = {}
    for it, ref in zip(items, refs):
        a = ans.get(it["id"])
        t = (a["translation"] if a else "") or ""
        g = it.get("gender")
        fp = first_person_gender(t)
        gender_ok = None if not (g and fp) else fp == {g}
        want = [names[w] for w in set(_mid_sentence_caps(it["en"])) if w in names]
        name_ok = None if not want else all(
            n.lower()[:max(3, len(n) - 2)].replace("ё", "е") in t.lower().replace("ё", "е")
            for n in want)
        per[it["id"]] = {"answered": bool(a), "chrf": chrf(t, ref), "gender_ok": gender_ok,
                         "name_ok": name_ok, "bucket": it["bucket"],
                         "defect": bool(a and a["rules_status"] != "translated"),
                         "cut": bool(a and (a["finish_reason"] or "") == "length")}
    return {"per": per}


def score(name: str, quiet: bool = False) -> dict:
    meta = json.loads((EXP_DIR / f"{name}.json").read_text(encoding="utf-8"))
    items = _load_set(meta["set"])
    ans = _answers(meta["job_id"])
    res = score_H(items, ans) if meta["set"] == "H" else score_D(items, ans)
    per = res["per"]
    n = len(per)
    got = sum(v["answered"] for v in per.values())
    summ = {"answered": got, "n": n,
            "defect": sum(v["defect"] for v in per.values()),
            "cut": sum(v["cut"] for v in per.values())}
    if meta["set"] == "H":
        for k in ("exact", "clean", "lemma"):
            summ[k] = round(100 * sum(v[k] for v in per.values()) / max(n, 1), 1)
    else:
        summ["chrf"] = round(sum(v["chrf"] for v in per.values()) / max(n, 1), 2)
        g = [v["gender_ok"] for v in per.values() if v["gender_ok"] is not None]
        nm = [v["name_ok"] for v in per.values() if v["name_ok"] is not None]
        summ["gender_ok"] = f"{sum(g)}/{len(g)}"
        summ["name_ok"] = f"{sum(nm)}/{len(nm)}"
        for b in ("pidgin", "names", "female", "male", "other"):
            vs = [v["chrf"] for v in per.values() if v["bucket"] == b]
            summ[f"chrf_{b}"] = round(sum(vs) / max(len(vs), 1), 1)
    meta["score"] = summ
    (EXP_DIR / f"{name}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
    if not quiet:
        print(f"{name} [{meta['set']}] {json.dumps(meta['config'], ensure_ascii=False)}",
              file=out)
        print("   " + "  ".join(f"{k}={v}" for k, v in summ.items()), file=out)
        if meta["set"] == "H":
            bt = res["by_type"]
            print("   по типам: " + "  ".join(
                f"{k} {100 * v['clean'] / v['n']:.0f}%" for k, v in
                sorted(bt.items(), key=lambda x: -x[1]["n"])), file=out)
    return res


def compare(a: str, b: str) -> None:
    ra, rb = score(a, quiet=True), score(b, quiet=True)
    set_a = json.loads((EXP_DIR / f"{a}.json").read_text(encoding="utf-8"))["set"]
    pa, pb = ra["per"], rb["per"]
    ids = [i for i in pa if i in pb and pa[i]["answered"] and pb[i]["answered"]]
    print(f"{a} против {b}: парных строк {len(ids)}", file=out)
    if set_a == "H":
        only_a = sum(1 for i in ids if pa[i]["clean"] and not pb[i]["clean"])
        only_b = sum(1 for i in ids if pb[i]["clean"] and not pa[i]["clean"])
        d = only_a + only_b
        z = abs(only_b - only_a) / math.sqrt(d) if d else 0.0
        print(f"   верно только в {a}: {only_a}   только в {b}: {only_b}   "
              f"z={z:.2f} {'значимо' if z >= 1.96 else 'не значимо'}", file=out)
    else:
        diffs = [pb[i]["chrf"] - pa[i]["chrf"] for i in ids]
        mean = sum(diffs) / max(len(diffs), 1)
        rnd = random.Random(0)
        boots = sorted(sum(rnd.choice(diffs) for _ in diffs) / len(diffs) for _ in range(2000))
        lo, hi = boots[50], boots[1949]
        print(f"   chrF {b} − {a}: {mean:+.2f}  (95% [{lo:+.2f}, {hi:+.2f}]) "
              f"{'значимо' if lo > 0 or hi < 0 else 'не значимо'}", file=out)
        for b_ in ("pidgin", "names", "female", "male", "other"):
            ds = [pb[i]["chrf"] - pa[i]["chrf"] for i in ids if pa[i]["bucket"] == b_]
            if ds:
                print(f"     {b_:<7} {sum(ds) / len(ds):+.2f} (n={len(ds)})", file=out)


def run(args) -> None:
    items = _load_set(args.set)
    opts = {"scope": "sweep", "machines": [BENCH], "string_ids": [x["id"] for x in items],
            "trace_full": True, "judge": bool(args.judge)}
    if args.context_parts is not None:
        opts["context_parts"] = [x for x in args.context_parts.split(",") if x]
    if args.batch_size:
        opts["batch_size"] = args.batch_size
    if args.max_tokens:
        opts["max_tokens"] = args.max_tokens
    params = json.loads(args.params) if args.params else {}
    job = _post("/jobs/create", {"type": "review_strings", "options": opts, "params": params})
    if not job.get("ok"):
        sys.exit(f"задание не создано: {job}")
    EXP_DIR.mkdir(parents=True, exist_ok=True)
    meta = {"name": args.name, "set": args.set, "job_id": job["job_id"],
            "created": time.time(), "config": {"params": params, "options": {
                k: v for k, v in opts.items() if k != "string_ids"}}}
    (EXP_DIR / f"{args.name}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                                               encoding="utf-8")
    print(f"{args.name}: задание {job['job_id']}, строк {len(items)}", file=out, flush=True)
    if args.no_wait:
        return
    t0, last = time.time(), -1
    while time.time() - t0 < args.timeout:
        n = len(_answers(job["job_id"]))
        if n != last:
            print(f"   {time.strftime('%H:%M:%S')}  ответов {n}/{len(items)}", file=out,
                  flush=True)
            last = n
        if n >= len(items):
            break
        time.sleep(30)
    score(args.name)


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--name", required=True)
    r.add_argument("--set", choices=("H", "D"), required=True)
    r.add_argument("--params")
    r.add_argument("--context-parts")
    r.add_argument("--batch-size", type=int)
    r.add_argument("--max-tokens", type=int)
    r.add_argument("--judge", action="store_true")
    r.add_argument("--timeout", type=int, default=6 * 3600)
    r.add_argument("--no-wait", action="store_true")
    s = sub.add_parser("score")
    s.add_argument("name")
    c = sub.add_parser("compare")
    c.add_argument("a")
    c.add_argument("b")
    args = ap.parse_args()
    if args.cmd == "run":
        run(args)
    elif args.cmd == "score":
        score(args.name)
    else:
        compare(args.a, args.b)


if __name__ == "__main__":
    main()
