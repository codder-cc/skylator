"""Перевод сценой против перевода строкой — на эталоне, где ответ известен.

Сейчас каждая реплика переводится одна: к ней прикладываются карточка говорящего и
две соседние реплики как справка, но переводит модель только эту строку. В сцене
модель переводит весь разговор разом — реплику игрока и все ответы на неё по порядку,
— и её собственные решения о том, как звать собеседника и как держать тон, видны ей в
соседних строках.

    A  строка: как в бою сейчас — карточка, соседние реплики, стиль, справка о моде;
    B  сцена: та же справка, но строки темы идут одним нумерованным батчем, и
       ответ на эталонную строку берётся из её места в сцене.

Сравнение парное, по одним и тем же строкам (см. context_holdout.py, почему иначе
нельзя).

    python scripts/scene_holdout.py --worker darwin-int00mac-7PKF2W --n 80
"""
from __future__ import annotations

import argparse
import io
import math
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
MAX_SCENE = 8


def scene_of(row, dlg):
    """[«plugin:FORMID»] сцены, где стоит эта реплика, и её место в ней."""
    key = f"{(row['esp_name'] or '').lower()}:{(row['form_id'] or '').upper()[-6:]}"
    key = (dlg.get("alias") or {}).get(key, key)
    topic = (dlg.get("of_topic") or {}).get(key)
    if not topic:
        return None, None
    answers = list((dlg.get("topics") or {}).get(topic, []))
    if key not in answers:
        return None, None
    scene = [topic] + answers
    i = scene.index(key)
    # окно вокруг строки, если тема длинная
    lo = max(0, min(i - MAX_SCENE // 2, len(scene) - MAX_SCENE))
    window = scene[lo:lo + MAX_SCENE]
    return window, window.index(key)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker", required=True)
    ap.add_argument("--n", type=int, default=80)
    args = ap.parse_args()

    hold = {en: ru for en, ru in CH.load_official().items() if CH._in_holdout(en)}
    con = sqlite3.connect(str(ROOT / "cache" / "translations.db"), timeout=180)
    con.row_factory = sqlite3.Row
    repo = CH._Repo(con)
    dlg = CH.DLG.load(CH.MODS, CH.GAME)
    spk = CH.SP.load(CH.MODS, CH.GAME, repo=repo)
    style_ex = CH.OC.build_examples(repo)
    talk_text = {}
    for t in con.execute("SELECT esp_name, form_id, original FROM strings WHERE "
                         "(rec_type='DIAL' AND field_type='FULL') OR "
                         "(rec_type='INFO' AND field_type='NAM1')"):
        k = f"{(t['esp_name'] or '').lower()}:{(t['form_id'] or '').upper()[-6:]}"
        talk_text.setdefault(k, t["original"] or "")

    rows, seen = [], set()
    for r in con.execute(
            "SELECT id, mod_name, esp_name, form_id, rec_type, field_type, original "
            "FROM strings WHERE rec_type='INFO' AND field_type='NAM1' "
            "AND LENGTH(original) BETWEEN 8 AND 300"):
        en = (r["original"] or "").strip()
        if en in hold and en not in seen:
            scene, pos = scene_of(r, dlg)
            if scene and len(scene) >= 3 and all(talk_text.get(k) for k in scene):
                seen.add(en)
                rows.append((r, scene, pos))
    random.Random(7).shuffle(rows)
    rows = rows[:args.n]
    print(f"строк эталона внутри сцены из 3+ реплик: {len(rows)}\n", file=out, flush=True)

    pairs = {"обе верно": 0, "обе мимо": 0, "только строка": 0, "только сцена": 0}
    lem = {"строка": 0, "сцена": 0}
    t0 = time.time()
    for n, (r, scene, pos) in enumerate(rows, 1):
        en = r["original"].strip()
        want = hold[en].strip()
        ctx_line = CH.context_for(r, dlg, spk, repo, talk_text, None, style_ex)
        term = _build_terminology([en])
        try:
            got = parse_numbered_output(CH.infer(args.worker, build_prompt(
                texts=[en], src_lang="English", tgt_lang="Russian",
                context=ctx_line, terminology=term)), 1)
            a = (got[0] if got else "").strip()
        except Exception as exc:                                   # noqa: BLE001
            print(f"  сбой строки: {exc}", file=out)
            a = ""
        # сцена: те же карточка/стиль/справка, но вместо «соседних реплик» — сам разговор
        ctx_scene = CH.context_for(r, dlg, spk, repo, talk_text,
                                   ("card", "summary", "style"), style_ex)
        texts = [talk_text[k] for k in scene]
        ctx_scene = (ctx_scene + "\n" if ctx_scene else "") + (
            "These lines are one conversation, in order: line 1 is what the player says, "
            "the rest are the character's replies. Translate them as one scene — keep "
            "names, forms of address and tone consistent across the lines.")
        try:
            got = parse_numbered_output(CH.infer(args.worker, build_prompt(
                texts=texts, src_lang="English", tgt_lang="Russian",
                context=ctx_scene, terminology=_build_terminology(texts))), len(texts))
            b = (got[pos] if pos < len(got) else "").strip()
        except Exception as exc:                                   # noqa: BLE001
            print(f"  сбой сцены: {exc}", file=out)
            b = ""
        ha, hb = CH.loose(a) == CH.loose(want), CH.loose(b) == CH.loose(want)
        lem["строка"] += CH.by_lemma(want, a)
        lem["сцена"] += CH.by_lemma(want, b)
        key = ("обе верно" if ha and hb else "обе мимо" if not (ha or hb)
               else "только строка" if ha else "только сцена")
        pairs[key] += 1
        if n % 10 == 0:
            print(f"  {n}/{len(rows)}  ({time.time() - t0:.0f} с)", file=out, flush=True)
        if n <= 6 or ha != hb:
            print(f"\n  [{key}] {en[:100]}\n     игра   {want[:100]}\n     строка {a[:100]}"
                  f"\n     сцена  {b[:100]}", file=out, flush=True)

    n = max(len(rows), 1)
    print(f"\nсовпало без огрехов: строка {100 * (pairs['обе верно'] + pairs['только строка']) / n:.1f}%"
          f"  сцена {100 * (pairs['обе верно'] + pairs['только сцена']) / n:.1f}%", file=out)
    print(f"по леммам: строка {100 * lem['строка'] / n:.1f}%  сцена {100 * lem['сцена'] / n:.1f}%",
          file=out)
    for k, v in pairs.items():
        print(f"  {k:<14}{v:>5}", file=out)
    disc = pairs["только строка"] + pairs["только сцена"]
    if disc:
        z = abs(pairs["только сцена"] - pairs["только строка"]) / math.sqrt(disc)
        print(f"  расхождений {disc}, z = {z:.2f} — "
              f"{'значимо' if z >= 1.96 else 'не отличимо от случайного'}", file=out)


if __name__ == "__main__":
    main()
