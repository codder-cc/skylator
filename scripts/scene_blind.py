"""Строка или сцена — слепое сравнение на настоящих диалогах модов.

На эталоне это не измерить: из 2 552 отложенных официальных пар в модовых диалогах
встречается 19 реплик, и лишь 6 из них стоят в сцене. Поэтому здесь сцены берутся
из самих модов, переводятся обоими способами, и пары выдаются для ручной разметки
в перемешанном порядке — какой вариант какой, записано отдельно.

    A  строка: каждая реплика отдельно, с карточкой, соседними репликами, стилем и
       справкой о моде — как в бою сейчас;
    B  сцена: реплика игрока и ответы на неё одним нумерованным батчем.

    python scripts/scene_blind.py make  --worker darwin-int00mac-7PKF2W --scenes 25
    python scripts/scene_blind.py score --labels labels.json
"""
from __future__ import annotations

import argparse
import io
import json
import random
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "remote_worker"))
sys.path.insert(0, str(ROOT / "scripts"))

import context_holdout as CH                                        # noqa: E402
from prompt.builder import build_prompt                             # noqa: E402
from prompt.parser import parse_numbered_output                     # noqa: E402
from translator.web.offline_backend import _build_terminology       # noqa: E402

out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
SCRATCH = Path(r"C:\Users\incro\AppData\Local\Temp\claude\H--Nolvus"
               r"\bb782997-3cef-41c8-89b4-86ec35a6f373\scratchpad")
SCENE_NOTE = ("These lines are one conversation, in order: line 1 is what the player "
              "says, the rest are the character's replies. Translate them as one scene — "
              "keep names, forms of address and tone consistent across the lines.")


def make(args) -> None:
    con = sqlite3.connect(str(ROOT / "cache" / "translations.db"), timeout=180)
    con.row_factory = sqlite3.Row
    repo = CH._Repo(con)
    dlg = CH.DLG.load(CH.MODS, CH.GAME)
    spk = CH.SP.load(CH.MODS, CH.GAME, repo=repo)
    style_ex = CH.OC.build_examples(repo)
    rows = {}
    for r in con.execute("SELECT id, mod_name, esp_name, form_id, rec_type, field_type, "
                         "original FROM strings WHERE (rec_type='DIAL' AND field_type='FULL')"
                         " OR (rec_type='INFO' AND field_type='NAM1')"):
        k = f"{(r['esp_name'] or '').lower()}:{(r['form_id'] or '').upper()[-6:]}"
        rows.setdefault(k, r)
    talk_text = {k: (r["original"] or "") for k, r in rows.items()}
    topics = [(t, a) for t, a in (dlg.get("topics") or {}).items()
              if t in rows and 2 <= len(a) <= 6
              and all(k in rows and 15 <= len(rows[k]["original"] or "") <= 250 for k in a)
              and 3 <= len(rows[t]["original"] or "") <= 120]
    random.Random(args.seed).shuffle(topics)
    topics = topics[:args.scenes]
    print(f"сцен: {len(topics)}", file=out, flush=True)

    items, key = [], {}
    t0 = time.time()
    for si, (topic, answers) in enumerate(topics, 1):
        scene = [topic] + list(answers)
        texts = [talk_text[k] for k in scene]
        first = rows[answers[0]]
        ctx_scene = CH.context_for(first, dlg, spk, repo, talk_text,
                                   ("card", "summary", "style"), style_ex)
        ctx_scene = (ctx_scene + "\n" if ctx_scene else "") + SCENE_NOTE
        try:
            b_all = parse_numbered_output(CH.infer(args.worker, build_prompt(
                texts=texts, src_lang="English", tgt_lang="Russian", context=ctx_scene,
                terminology=_build_terminology(texts))), len(texts))
        except Exception as exc:                                   # noqa: BLE001
            print(f"  сбой сцены: {exc}", file=out)
            continue
        for li, k in enumerate(scene):
            r = rows[k]
            ctx = CH.context_for(r, dlg, spk, repo, talk_text, None, style_ex)
            try:
                got = parse_numbered_output(CH.infer(args.worker, build_prompt(
                    texts=[r["original"]], src_lang="English", tgt_lang="Russian",
                    context=ctx, terminology=_build_terminology([r["original"]]))), 1)
                a = (got[0] if got else "").strip()
            except Exception as exc:                               # noqa: BLE001
                print(f"  сбой строки: {exc}", file=out)
                continue
            b = (b_all[li] if li < len(b_all) else "").strip()
            if not a or not b or a == b:
                continue
            flip = random.Random(hash((si, li, args.seed))).random() < 0.5
            iid = f"{si}.{li}"
            key[iid] = "BA" if flip else "AB"
            items.append({"id": iid, "scene": si, "line": li, "mod": r["mod_name"],
                          "english": texts, "target": li,
                          "X": b if flip else a, "Y": a if flip else b})
        print(f"  {si}/{len(topics)}  ({time.time() - t0:.0f} с, пар {len(items)})",
              file=out, flush=True)
    (SCRATCH / "scene_blind_items.json").write_text(
        json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    (SCRATCH / "scene_blind_key.json").write_text(json.dumps(key), encoding="utf-8")
    print(f"пар для разметки: {len(items)} → scene_blind_items.json", file=out)


def score(args) -> None:
    key = json.loads((SCRATCH / "scene_blind_key.json").read_text(encoding="utf-8"))
    labels = json.loads(Path(args.labels).read_text(encoding="utf-8"))
    tally = {"строка": 0, "сцена": 0, "равно": 0}
    for iid, lab in labels.items():
        if lab == "=":
            tally["равно"] += 1
            continue
        order = key[iid]                       # "AB": X=строка, Y=сцена
        winner = order[0] if lab == "X" else order[1]
        tally["строка" if winner == "A" else "сцена"] += 1
    print(tally, file=out)
    import math
    d = tally["строка"] + tally["сцена"]
    if d:
        z = abs(tally["сцена"] - tally["строка"]) / math.sqrt(d)
        print(f"z = {z:.2f} ({'значимо' if z >= 1.96 else 'не отличимо от случайного'})",
              file=out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("make", "score"))
    ap.add_argument("--worker")
    ap.add_argument("--scenes", type=int, default=25)
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--labels")
    args = ap.parse_args()
    (make if args.mode == "make" else score)(args)


if __name__ == "__main__":
    main()
