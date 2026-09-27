"""Судья, который видит говорящего, против судьи, который видит только текст.

Чтение того, что одобрил нынешний судья, показало три слепых пятна: пол говорящего,
ломаную речь и соседние реплики. Реплика Берга «Him use metal club to smack behind»
в новом переводе стала «Тот ударил его в спину» — смысл перевёрнут, а судья выбрал её,
потому что русский глаже; в его инструкции прямо сказано, что неверная грамматика —
серьёзная ошибка.

Здесь оба судьи спрашиваются на ОДНИХ И ТЕХ ЖЕ парах, в обоих порядках:

    A  нынешний: исходник, два перевода, официальные имена строки;
    B  с говорящим: то же плюс карточка (пол, раса, ломаная речь) и соседние реплики,
       а в инструкции смысл и голос персонажа стоят выше гладкости.

Пары — два размеченных набора: 100 случайных из слоя (judge_set) и 101 трудная
(hard_set: ломаная речь, род, имена, «не уверен», «хранимый лучше»). Разметка ручная:
C — лучше новый, S — лучше хранимый; равноценные и неразрешимые без пола в замер не
идут.

    python scripts/judge_speaker_test.py --worker darwin-int00mac-7PKF2W
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "remote_worker"))
sys.path.insert(0, str(ROOT / "scripts"))

import context_holdout as CH                                        # noqa: E402
from judge_layer_test import LABELS as SET_LABELS                   # noqa: E402
from judge_pairs import infer                                       # noqa: E402
from prompt.builder import build_judge_prompt, build_raw_chatml     # noqa: E402
from translator.data_manager.string_manager import _identity_from_key  # noqa: E402
from translator.db import promote as P                              # noqa: E402

out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
SCRATCH = Path(r"C:\Users\incro\AppData\Local\Temp\claude\H--Nolvus"
               r"\bb782997-3cef-41c8-89b4-86ec35a6f373\scratchpad")

# Ручная разметка hard_set.json (индекс → C/S). Остальное — равноценно (E), пол
# говорящего неизвестен (G) или реплика самого игрока (X) — в замер не идёт.
HARD = {}
for i in (1, 3, 4, 5, 6, 7, 8, 9, 12, 40, 41, 51, 52, 54, 55, 56, 62, 63, 75, 78, 79,
          81, 89):
    HARD[i] = "C"
for i in (2, 11, 13, 14, 17, 18, 24, 31, 33, 34, 35, 37, 39, 42, 44, 45, 47, 48, 49,
          69, 74, 83, 84, 88, 92, 96, 99, 100):
    HARD[i] = "S"

SYSTEM_B = (
    "You are a Russian editor for the Russian localization of Skyrim. You are given an "
    "English source line, facts about who speaks it, the lines around it in the "
    "conversation, and two Russian renderings. Choose the better one.\n"
    "What matters, in this order:\n"
    "1. Meaning: who does what to whom must match the English. A fluent line with the "
    "wrong meaning is worse than a clumsy line with the right one.\n"
    "2. The speaker: first-person forms must match the speaker's gender. If the speaker "
    "talks in broken, ungrammatical language on purpose, the Russian must stay broken "
    "the same way — a line that corrects the speaker's grammar is WRONG, however smooth.\n"
    "3. Names and terms as in the official Russian Skyrim localization.\n"
    "4. Natural Russian: no calques, no garbage characters, no leftover English.\n"
    "If both are equally good, prefer A. Reply with exactly one character: A or B."
)


def names_block(original: str) -> list:
    got = []
    for w in sorted(set(re.findall(r"\b[A-Z][a-z'\-]{3,}\b", original or ""))):
        ru = P._names().get(w)
        if ru:
            got.append((w, ru))
    return got


def prompt_a(item, a, b):
    names = item["names"]
    ent = ("Names the game already has — use exactly these, declined as Russian grammar "
           "requires: " + "; ".join(f"{en} = {ru}" for en, ru in names)) if names else ""
    return build_judge_prompt(item["original"], a, b, ent)


def prompt_b(item, a, b):
    user = f"English source:\n{item['original']}\n\n"
    if item["context"]:
        user += f"{item['context']}\n\n"
    if item["pidgin"] and "broken" not in item["context"]:
        user += ("This line is written in deliberately broken language; the Russian "
                 "must keep it broken.\n\n")
    if item["names"]:
        user += "Official Russian names in this line:\n" + "\n".join(
            f"  {en} → {ru}" for en, ru in item["names"]) + "\n\n"
    user += f"A:\n{a}\n\nB:\n{b}\n\nWhich is better? Answer A or B."
    return build_raw_chatml(SYSTEM_B, user)


def _facts(item) -> str:
    """Только факты о говорящем: пол и ломаная речь, без разговора и словаря."""
    bits = []
    for line in (item["context"] or "").splitlines():
        if line.startswith(("Speaker:", "The player is speaking TO:")):
            bits.append(line)
    if item["pidgin"]:
        bits.append("This speaker talks in deliberately broken language.")
    return "\n".join(bits)


def _user_with(item, a, b, extra):
    user = f"English source:\n{item['original']}\n\n"
    if extra:
        user += extra + "\n\n"
    if item["names"]:
        user += "Official Russian names in this line:\n" + "\n".join(
            f"  {en} → {ru}" for en, ru in item["names"]) + "\n\n"
    return user + f"A:\n{a}\n\nB:\n{b}\n\nWhich is better? Answer A or B."


def prompt_b2(item, a, b):
    """Инструкция прежняя, контекст полный: помогает ли сам контекст."""
    from prompt.builder import _JUDGE_SYSTEM
    return build_raw_chatml(_JUDGE_SYSTEM, _user_with(item, a, b, item["context"]))


def prompt_b3(item, a, b):
    """Инструкция прежняя, только факты о говорящем."""
    from prompt.builder import _JUDGE_SYSTEM
    return build_raw_chatml(_JUDGE_SYSTEM, _user_with(item, a, b, _facts(item)))


def ask(worker, build, item):
    def once(a, b):
        got = infer(worker, build(item, a, b)).upper()
        return next((ch for ch in got if ch in "AB"), "?")
    v1 = once(item["s"], item["c"])        # A = хранимый
    v2 = once(item["c"], item["s"])        # A = новый
    if v1 == "B" and v2 == "A":
        return "C"
    if v1 == "A" and v2 == "B":
        return "S"
    return "?"


def load_items(con):
    dlg = CH.DLG.load(CH.MODS, CH.GAME)
    spk = CH.SP.load(CH.MODS, CH.GAME, repo=CH._Repo(con))
    talk_text = {}
    for t in con.execute("SELECT esp_name, form_id, original FROM strings WHERE "
                         "(rec_type='DIAL' AND field_type='FULL') OR "
                         "(rec_type='INFO' AND field_type='NAM1')"):
        k = f"{(t['esp_name'] or '').lower()}:{(t['form_id'] or '').upper()[-6:]}"
        talk_text.setdefault(k, t["original"] or "")
    items = []

    def add(cand, gold, bucket, s, c):
        ident = _identity_from_key(cand["key"] or "")
        row = {"esp_name": cand["esp_name"], "form_id": (ident[0] if ident else ""),
               "rec_type": cand["rec_type"], "field_type": cand["field_type"],
               "original": cand["original"], "mod_name": cand["mod_name"]}
        ctx = CH.context_for(row, dlg, spk, None, talk_text, ("card", "talk"), None)
        card = CH.SP.card_for(row["esp_name"], row["form_id"] or "", spk)
        g = CH.SP.gender_for(row["esp_name"], row["form_id"] or "")
        if g is None and card is not None and card.sex in ("m", "f"):
            g = card.sex
        items.append({"gender": g, "gold": gold, "bucket": bucket, "original": cand["original"],
                      "s": s, "c": c, "context": ctx, "names": names_block(cand["original"]),
                      "pidgin": bool(card and card.pidgin) or P.looks_pidgin(cand["original"])})

    hard = json.loads((SCRATCH / "hard_set.json").read_text(encoding="utf-8"))
    for i, (why, r) in enumerate(hard):
        if i in HARD:
            add(r, HARD[i], why.split(":")[0], r["rival"] or r["stored_at_arrival"] or "",
                r["translation"])
    js = json.loads((SCRATCH / "judge_set.json").read_text(encoding="utf-8"))
    for i, p in enumerate(js):
        gold = SET_LABELS.get(i)
        if gold not in ("C", "S"):
            continue
        cand = con.execute("SELECT * FROM candidates WHERE id=?", (p["id"],)).fetchone()
        if cand:
            add(cand, gold, "random", p["s"], p["t"])
    return items


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker", required=True)
    ap.add_argument("--variants", default="A,B")
    args = ap.parse_args()
    con = sqlite3.connect(str(ROOT / "cache" / "translations.db"), timeout=120)
    con.row_factory = sqlite3.Row
    items = load_items(con)
    with_ctx = sum(bool(x["context"]) for x in items)
    print(f"пар в замере: {len(items)} (с карточкой или разговором: {with_ctx}, "
          f"ломаная речь: {sum(x['pidgin'] for x in items)})", file=out, flush=True)
    builders = {"A": prompt_a, "B": prompt_b, "B2": prompt_b2, "B3": prompt_b3}
    results = {}
    for v in args.variants.split(","):
        t0 = time.time()
        tally = collections.defaultdict(collections.Counter)
        verdicts = []
        for it in items:
            try:
                got = ask(args.worker, builders[v], it)
            except Exception as exc:                               # noqa: BLE001
                print(f"  сбой: {exc}", file=out, flush=True)
                got = "?"
            verdicts.append(got)
            k = "верно" if got == it["gold"] else "не уверен" if got == "?" else "ОШИБКА"
            tally[it["bucket"]][k] += 1
            tally["ВСЕГО"][k] += 1
            # порча = судья пропустил новый, когда лучше хранимый
            if got == "C" and it["gold"] == "S":
                tally["ВСЕГО"]["пропущено порчи"] += 1
            if got == "C" and it["gold"] == "C":
                tally["ВСЕГО"]["взято улучшений"] += 1
        results[v] = verdicts
        # Итог политики: в корпус идёт только «новый» от судьи, прошедший фильтры.
        pol_bad = pol_good = 0
        for it, got in zip(items, verdicts):
            if got != "C":
                continue
            row = {"judge": "fresh", "rules_status": "translated", "rival": it["s"],
                   "stored_at_arrival": it["s"], "translation": it["c"],
                   "original": it["original"]}
            ok, _why = P.decide(row, speaker_gender=it.get("gender"),
                                speaker_pidgin=it["pidgin"])
            if ok and it["gold"] == "S":
                pol_bad += 1
            if ok and it["gold"] == "C":
                pol_good += 1
        (SCRATCH / f"judge_verdicts_{v}.json").write_text(
            json.dumps(verdicts), encoding="utf-8")
        print(f"\n=== судья {v}: {time.time() - t0:.0f} с", file=out)
        for b, c in sorted(tally.items(), key=lambda x: x[0] != "ВСЕГО"):
            print(f"  {b:<8} верно {c['верно']:>3}  ошибка {c['ОШИБКА']:>3}  "
                  f"не уверен {c['не уверен']:>3}", file=out)
        tot = tally["ВСЕГО"]
        n_s = sum(1 for x in items if x["gold"] == "S")
        n_c = sum(1 for x in items if x["gold"] == "C")
        print(f"  пропущено порчи: {tot['пропущено порчи']} из {n_s}; "
              f"взято улучшений: {tot['взято улучшений']} из {n_c}", file=out)
        print(f"  ПОСЛЕ ФИЛЬТРОВ в корпус: порчи {pol_bad} из {n_s}, "
              f"улучшений {pol_good} из {n_c}", file=out, flush=True)
    if False:
        print("\nгде судьи разошлись:", file=out)
        for it, a, b in zip(items, results["A"], results["B"]):
            if a != b:
                print(f"  [{it['bucket']}] эталон {it['gold']}: A={a} B={b}  "
                      f"{it['original'][:90]}", file=out)


if __name__ == "__main__":
    main()
