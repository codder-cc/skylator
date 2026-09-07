"""
Measure a review prompt before it is dispatched.

238 000 pairs went out on a prompt nobody had tested. They came back having corrected
0.23% of them, against a defect rate measured at roughly one in seven — and more than
half of what did change was double spaces. The framing, "output the corrected
translation, or the same one if it is already right", makes copying the input a
well-formed answer for every line, and the model took it. Two days of two machines to
learn what eighteen strings say in a minute.

So a prompt earns its dispatch here first: it has to catch known errors and leave
known-good work alone. Runs against a live agent through the raw inference chunk and
writes nothing to the database.

    python scripts/review_bench.py --variant shipped   # what is deployed
    python scripts/review_bench.py --variant verdict   # a decision per line
    python scripts/review_bench.py --variant blind     # translate again, unaided
    python scripts/review_bench.py --variant pipeline  # blind, then choose between the two
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "remote_worker"))

MASTER = "http://127.0.0.1:5000"
CONTROL = ROOT / "tests" / "data" / "review_control_set.json"


def _get(path: str, timeout: int = 30):
    return json.load(urllib.request.urlopen(f"{MASTER}{path}", timeout=timeout))


def _post(path: str, payload: dict, timeout: int = 600):
    req = urllib.request.Request(f"{MASTER}{path}", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def pick_agent(prefer: str | None = None) -> str:
    """The fastest free agent — a busy one refuses raw inference by design."""
    ws = _get("/api/workers")
    free = [w for w in ws if not (w.get("offline_jobs") or [])]
    if prefer:
        free = [w for w in free if prefer in w["label"]] or free
    if not free:
        raise SystemExit("no free agent — cancel its offline package first")
    free.sort(key=lambda w: float((w.get("stats") or {}).get("tps_avg") or 0), reverse=True)
    return free[0]["label"]


def run_prompt(label: str, prompt: str, timeout: int = 900) -> str:
    """One raw inference chunk, the text back. Touches no stored data."""
    return _post(f"/api/workers/{label}/infer",
                 {"prompt": prompt, "timeout": timeout,
                  "params": {"temperature": 0.2, "max_tokens": 4096}},
                 timeout=timeout + 60)["result"]


def numbered(out: str, n: int) -> dict[int, str]:
    got: dict[int, str] = {}
    for line in (out or "").splitlines():
        m = re.match(r"\s*(\d+)\s*[.):]\s*(.*)$", line)
        if not m:
            continue
        i, rest = int(m.group(1)) - 1, m.group(2).strip()
        if 0 <= i < n and rest:
            got[i] = rest
    return got


# ── stage one: find the suspects ─────────────────────────────────────────────

def build_stage1(variant: str, pairs: list[dict]) -> str:
    from prompt.builder import build_prompt, build_raw_chatml
    texts = [p["en"] for p in pairs]

    if variant == "shipped":
        return build_prompt(texts, "English", "Russian", current=[p["ru"] for p in pairs])

    if variant in ("blind", "pipeline"):
        # Never show the stored answer. What the model cannot see it cannot copy, so it
        # has to translate — and the disagreement with what is stored is the signal.
        return build_prompt(texts, "English", "Russian")

    if variant == "verdict":
        system = ("You are a strict reviewer of Russian translations for The Elder Scrolls V: "
                  "Skyrim. You are shown translations accepted without review; a measured one "
                  "in seven is wrong. Assume errors are present and find them.")
        lines = "\n".join(f"{i+1}. {p['en']}  =>  {p['ru']}" for i, p in enumerate(pairs))
        user = ("For each numbered pair, decide whether the Russian is a correct translation "
                "of the English.\n\nOne line per number, exactly:\n"
                "  N. OK\n  N. FIX <corrected Russian>\n\n"
                "Use FIX for: invented words, a proper noun rendered as a different name or "
                "misspelled, a word meaning something else than the English (a forge is not "
                "an anvil, a mace is not a club), Latin letters inside a Russian word, "
                "untranslated English, broken grammar, omissions.\n"
                "Never repeat the English. Never explain.\n\nPairs:\n" + lines)
        return build_raw_chatml(system, user)

    if variant == "backtrans":
        # Translate the stored Russian back to English and compare with the source. The
        # model is never asked to judge — only to translate, which is what it is good at.
        # Meaning drift shows up as a back-translation that does not match: «на наковальне»
        # comes back as "on the anvil", not "at a forge". A legitimate synonym comes back
        # matching, which is what separates a wrong translation from a different one.
        system = ("You are a translator. Translate Russian game text into plain English. "
                  "Translate literally and completely. Output only the numbered translations.")
        lines = "\n".join(f"{i+1}. {p['ru']}" for i, p in enumerate(pairs))
        user = ("Translate each numbered Russian string into English.\n"
                "One line per number, the English only. Never explain.\n\n" + lines)
        return build_raw_chatml(system, user)

    raise SystemExit(f"unknown variant {variant}")


def english_similarity(a: str, b: str) -> float:
    """Word overlap between two English strings, order-free and case-free."""
    def words(s: str) -> set[str]:
        return set(re.findall(r"[a-z0-9]+", (s or "").lower()))
    wa, wb = words(a), words(b)
    if not wa and not wb:
        return 1.0
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def parse_stage1(variant: str, out: str, n: int) -> dict[int, str | None]:
    """{index: proposed text}; None means the model left it alone."""
    raw = numbered(out, n)
    if variant in ("shipped", "blind", "pipeline"):
        return dict(raw)
    got: dict[int, str | None] = {}
    for i, rest in raw.items():
        if rest.upper().startswith("OK"):
            got[i] = None
        elif rest.upper().startswith("FIX"):
            got[i] = rest[3:].strip(" :") or None
        else:
            got[i] = rest
    return got


# ── stage two: choose between two concrete candidates ────────────────────────

def build_stage2(triples: list[dict]) -> str:
    """The English and two candidates — pick one, or write a third.

    Choosing is a different task from judging. Judging one translation invites agreement:
    the shipped prompt called 89% of known-bad strings fine. A choice has no default —
    both options are on the table and one of them has to be named.
    """
    from prompt.builder import build_raw_chatml
    system = ("You are a senior reviewer of Russian translations for The Elder Scrolls V: "
              "Skyrim. Two translators disagreed. Decide which one is right.")
    rules = "\n".join([
        "For each numbered item you get the English and two candidate Russian translations.",
        "Exactly one line per number, in one of these shapes:",
        "  N. A",
        "  N. B",
        "  N. C <a better Russian translation, when both A and B are wrong>",
        "",
        "Judge on: invented or non-existent words; a proper noun rendered as a different",
        "name or misspelled; a word that means something else than the English (a forge is",
        "not an anvil, a mace is not a club); Latin letters inside a Russian word;",
        "untranslated English; broken grammar; anything omitted.",
        "When both are acceptable Russian, answer A.",
        "Keep tokens such as <Alias=...>, %1 and NL markers exactly. Never explain.",
        "",
    ])
    blocks = []
    for i, t in enumerate(triples):
        blocks.append("{}. EN: {}\n   A: {}\n   B: {}".format(i + 1, t["en"], t["a"], t["b"]))
    return build_raw_chatml(system, rules + "\n".join(blocks))


def build_equiv(pairs_en: list[tuple[str, str]]) -> str:
    """Two English texts — do they mean the same thing?

    The Russian is never shown. Word overlap cannot do this job: it scores a correct
    «Трактир "Замёрзший Фрукт"» at 0.00 against "Frostfruit Inn" and a wrong «Киродил» at
    1.00 against "Cyrodiil", because it counts letters rather than meaning. Comparing two
    English sentences for the same meaning is a task these models are good at, and there
    is nothing here to anchor on — neither text is presented as the answer.
    """
    from prompt.builder import build_raw_chatml
    system = ("You compare two English texts and say whether they mean the same thing. "
              "You are terse and literal.")
    rules = "\n".join([
        "For each numbered item you get an ORIGINAL English text and a CANDIDATE that came",
        "back from a round trip through another language.",
        "",
        "Answer one line per number, exactly:",
        "  N. SAME",
        "  N. DIFF <what changed, three words>",
        "",
        "Answer DIFF when the candidate names a different thing, place, person or material",
        "(a forge is not an anvil, a mace is not a club, Skyrim is not Cyrodiil), when a",
        "name is spelled as a different name, when something in the original is missing,",
        "or when the meaning is changed.",
        "Answer SAME for wording, word order, articles, capitalisation, or a synonym that",
        "means the same thing.",
        "Never explain beyond the three words.",
        "",
    ])
    blocks = []
    for i, (src, cand) in enumerate(pairs_en):
        blocks.append("{}. ORIGINAL: {}\n   CANDIDATE: {}".format(i + 1, src, cand))
    return build_raw_chatml(system, rules + "\n".join(blocks))


def parse_equiv(out: str, n: int) -> dict[int, bool]:
    """{index: True if the model called it different}"""
    got: dict[int, bool] = {}
    for i, rest in numbered(out, n).items():
        got[i] = rest.upper().startswith("DIFF")
    return got


def parse_stage2(out: str, n: int) -> dict[int, str]:
    got: dict[int, str] = {}
    for i, rest in numbered(out, n).items():
        head = rest.upper()
        if head.startswith("A"):
            got[i] = "A"
        elif head.startswith("B"):
            got[i] = "B"
        elif head.startswith("C"):
            got[i] = rest[1:].strip(" :") or "A"
        else:
            got[i] = rest
    return got


# ── scoring ──────────────────────────────────────────────────────────────────

def score(pairs: list[dict], final: dict[int, str | None], nb: int, ng: int) -> None:
    caught = missed = kept = churned = 0
    for i, p in enumerate(pairs):
        answer = final.get(i)
        changed = answer is not None and answer.strip() != p["ru"].strip()
        if p["expect"] == "fix":
            if changed:
                caught += 1
                print(f"  ПОЙМАЛ    {p['en'][:44]}")
                print(f"            было  {p['ru'][:62]}")
                print(f"            стало {answer[:62]}")
            else:
                missed += 1
                print(f"  ПРОПУСТИЛ {p['en'][:42]}   ({p['why']})")
        else:
            if changed:
                churned += 1
                print(f"  ИСПОРТИЛ  {p['en'][:42]}")
                print(f"            было  {p['ru'][:62]}")
                print(f"            стало {answer[:62]}")
            else:
                kept += 1
    print(f"\n  поймано {caught}/{nb} ошибок ({caught/nb*100:.0f}%)")
    print(f"  сохранено {kept}/{ng} верных, испорчено {churned}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", default="shipped",
                    choices=["shipped", "verdict", "blind", "pipeline", "backtrans", "equiv"])
    ap.add_argument("--agent", default=None)
    args = ap.parse_args()

    data = json.loads(CONTROL.read_text(encoding="utf-8"))
    pairs = ([dict(p, expect="fix") for p in data["bad"]] +
             [dict(p, expect="keep") for p in data["good"]])
    nb, ng = len(data["bad"]), len(data["good"])
    label = args.agent or pick_agent()
    print(f"агент: {label}   вариант: {args.variant}   пар: {len(pairs)}\n")

    if args.variant == "equiv":
        back = numbered(run_prompt(label, build_stage1("backtrans", pairs)), len(pairs))
        ens = [(p["en"], back.get(i, "")) for i, p in enumerate(pairs)]
        flagged = parse_equiv(run_prompt(label, build_equiv(ens)), len(pairs))
        caught = missed = kept = churned = 0
        for i, p in enumerate(pairs):
            diff = flagged.get(i, False)
            if p["expect"] == "fix":
                if diff:
                    caught += 1
                    print(f"  ПОЙМАЛ    {p['en'][:36]:<36} -> {back.get(i,'')[:36]}")
                else:
                    missed += 1
                    print(f"  ПРОПУСТИЛ {p['en'][:36]:<36} -> {back.get(i,'')[:36]}")
            else:
                if diff:
                    churned += 1
                    print(f"  ЛОЖНАЯ    {p['en'][:36]:<36} -> {back.get(i,'')[:36]}")
                else:
                    kept += 1
        print(f"\n  поймано {caught}/{nb} ошибок ({caught/nb*100:.0f}%)")
        print(f"  верных не тронуто {kept}/{ng}, ложных срабатываний {churned}")
        return

    out = run_prompt(label, build_stage1(args.variant, pairs))

    if args.variant == "backtrans":
        back = numbered(out, len(pairs))
        rows = []
        for i, p in enumerate(pairs):
            sim = english_similarity(p["en"], back.get(i, ""))
            rows.append((sim, p["expect"], p["en"], p["ru"], back.get(i, "")))
        rows.sort(key=lambda r: r[0])
        print("  сходство  ожидание  английский -> обратный перевод")
        for sim, exp, en, ru, bt in rows:
            mark = "ПЛОХАЯ" if exp == "fix" else "верная"
            print(f"   {sim:5.2f}   {mark:<7} {en[:34]:<34} -> {bt[:38]}")
        bad = sorted(r[0] for r in rows if r[1] == "fix")
        good = sorted(r[0] for r in rows if r[1] == "keep")
        print("")
        print(f"  плохие:  медиана {bad[len(bad)//2]:.2f}  диапазон {bad[0]:.2f}..{bad[-1]:.2f}")
        print(f"  верные:  медиана {good[len(good)//2]:.2f}  диапазон {good[0]:.2f}..{good[-1]:.2f}")
        best = None
        for t in [x / 100 for x in range(5, 100, 5)]:
            caught = sum(1 for x in bad if x < t)
            churn  = sum(1 for x in good if x < t)
            if best is None or (caught - churn) > best[1]:
                best = (t, caught - churn, caught, churn)
        t, _, caught, churn = best
        print(f"  лучший порог {t:.2f}: поймано {caught}/{len(bad)}, "
              f"ложных {churn}/{len(good)}")
        return

    proposed = parse_stage1(args.variant, out, len(pairs))
    if args.variant != "pipeline":
        score(pairs, proposed, nb, ng)
        return

    # Stage one only says "these two answers differ". Stage two decides which is right.
    suspects = [i for i, p in enumerate(pairs)
                if proposed.get(i) and proposed[i].strip() != p["ru"].strip()]
    print(f"  шаг 1: расхождений {len(suspects)} из {len(pairs)}\n")
    triples = [{"en": pairs[i]["en"], "a": pairs[i]["ru"], "b": proposed[i]} for i in suspects]
    chosen = parse_stage2(run_prompt(label, build_stage2(triples)), len(triples))

    final: dict[int, str | None] = {}
    for k, i in enumerate(suspects):
        pick = chosen.get(k, "A")
        final[i] = None if pick == "A" else (proposed[i] if pick == "B" else pick)
    score(pairs, final, nb, ng)


if __name__ == "__main__":
    main()
