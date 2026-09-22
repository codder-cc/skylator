"""Помогает ли контекст — на отложенном срезе, где ответ известен.

Вопрос стоял дважды и оба раза остался без ответа. Сперва опыт на шести строках дал
разницу на уровне выбора слова; потом выяснилось, что в бою контекста не было вовсе —
он терялся дважды по дороге, и мерить было нечего. Теперь он доходит, и вопрос можно
закрыть честно.

Мерка объективная: 2 552 пары официальной локализации, срезанные по хешу и не
входящие ни в таблицу авторитета, ни в реестр, ни в промпты. Совпадение с ними — это
совпадение с текстом, который русский игрок видит в базовой игре.

    точно        строка в строку
    без огрехов  без учёта регистра, пунктуации и ё/е
    по леммам    каждое значащее слово эталона есть в ответе в какой-нибудь форме

Обе ветки получают глоссарий: он доезжал всегда, и разница должна быть в одном — в
контексте. Ветка «без контекста» это ровно то, что уходило в бой до сегодняшних
правок.

    python scripts/context_holdout.py --worker darwin-int00mac-7PKF2W --n 120
"""
from __future__ import annotations

import argparse
import io
import json
import os
import random
import sqlite3
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "remote_worker"))

os.environ["SKYLATOR_APPLY_HOLDOUT"] = "1"      # ответник нужен целиком

from prompt.builder import build_prompt                          # noqa: E402
from prompt.parser import parse_numbered_output                  # noqa: E402
from translator.characters import dialogue as DLG                # noqa: E402
from translator.characters import speakers as SP                 # noqa: E402
from translator.context import mod_summary as MS                 # noqa: E402
from translator.validation import official_context as OC         # noqa: E402
from translator.validation.authority import _in_holdout, load_official  # noqa: E402
from translator.validation.terminology import _lemma, _text_lemmas      # noqa: E402
from translator.web.offline_backend import _build_terminology     # noqa: E402

out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
MODS = Path(r"H:\Nolvus\Instances\Nolvus Awakening\MODS\mods")
GAME = Path(r"H:\Nolvus\Instances\Nolvus Awakening\STOCK GAME\Data")
LABELS = {"answer": "the character answers:", "topic": "the player says:",
          "prev": "just before, they said:"}

import re  # noqa: E402

_TRIM = re.compile(r"[\s\.:;,!?«»\"'()\[\]\-–—]+")
_WORD = re.compile(r"[А-Яа-яЁё]{3,}")


class _Repo:
    def __init__(self, db):
        self.db = db


def loose(text: str) -> str:
    return _TRIM.sub("", (text or "").lower().replace("ё", "е"))


def by_lemma(want: str, got: str) -> bool:
    need = _WORD.findall((want or "").lower())
    if not need:
        return False
    have = _text_lemmas(got or "")
    return all(_lemma(w) & have for w in need)


def infer(label: str, prompt: str) -> str:
    body = json.dumps({"prompt": prompt, "timeout": 300,
                       "params": {"temperature": 0.3, "top_k": 20, "top_p": 0.9,
                                  "repetition_penalty": 1.05, "max_tokens": 1024,
                                  "thinking": False}}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:5000/api/workers/{label}/infer", data=body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=330) as r:
        p = json.load(r)
    if not p.get("ok"):
        raise RuntimeError(p.get("error") or "нет ответа")
    return p.get("result") or ""


def context_for(row, dlg, spk, repo, talk_text, parts=None, style_ex=None) -> str:
    """Тот же порядок, что и в раздаче: карточка первой, справка о моде следом.

    `parts` позволяет проверять рычаги по отдельности. Это не педантизм: сегодня
    выяснилось, что ни один из них до модели не доходил, и меряя их скопом, мы
    узнаем только сумму — а слагаемые могут иметь разные знаки.
    """
    want = set(parts or ("card", "summary", "talk", "style"))
    bits = []
    ref = DLG.addressee_ref(row["esp_name"], row["form_id"], row["rec_type"],
                            row["field_type"], dlg)
    card = None
    if ref and "card" in want:
        plug, fid = ref.split(":", 1)
        card = SP.card_for(plug, fid, spk)
        if card:
            bits.append(card.addressee_block())
    if not card and "card" in want:
        block = SP.block_for(row["esp_name"], row["form_id"], spk)
        if block:
            bits.append(block)
    if "style" in want and style_ex:
        # Как игра формулирует записи ЭТОГО типа, её собственными парами. До модели
        # это не доходило никогда — терялось вместе с остальным контекстом, — а бьёт
        # оно ровно по слабым классам: ACTI «Read = Прочесть:», «Investigate =
        # Обследовать» это конвенция, которую из общего промпта не вывести.
        st = OC.style_block(row["rec_type"] or "", style_ex)
        if st:
            bits.append(st)
    # Как в бою: справка о моде коротким строкам не достаётся. Про «Iron Sword» она
    # не говорит ничего, а платится на каждом запросе — и первый замер с контекстом я
    # провёл именно с ней на всех подряд, то есть померил настройку, которой нет.
    if "summary" in want and len((row["original"] or "")) > 60:
        summary = MS.build(repo, row["mod_name"])
        if summary:
            bits.append(summary)
    talk = []
    for role, rk in ([] if "talk" not in want else DLG.neighbours(row["esp_name"], row["form_id"],
                                   row["rec_type"], row["field_type"], dlg)):
        txt = (talk_text.get(rk) or "").strip()
        if txt:
            talk.append(f'  (1) {LABELS[role]} "{txt[:180]}"')
    if talk:
        bits.append("Conversation around these lines:\n" + "\n".join(talk))
    return "\n".join(bits)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker", required=True)
    ap.add_argument("--n", type=int, default=120)
    ap.add_argument("--types", help="только эти типы записей, через запятую")
    ap.add_argument("--parts", default="card,summary,talk,style",
                    help="какие части контекста давать: card,summary,talk,style")
    args = ap.parse_args()

    full = load_official()
    hold = {en: ru for en, ru in full.items() if _in_holdout(en)}
    print(f"ответник: {len(hold):,} пар", file=out)

    con = sqlite3.connect(str(ROOT / "cache" / "translations.db"), timeout=180)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=180000")
    con.execute("PRAGMA cache_size=-400000")
    repo = _Repo(con)

    rows, seen = [], set()
    for r in con.execute(
            "SELECT id, mod_name, esp_name, form_id, rec_type, field_type, original "
            "FROM strings WHERE TRIM(COALESCE(original,'')) <> '' "
            "AND LENGTH(original) BETWEEN 8 AND 300"):
        en = (r["original"] or "").strip()
        if en in hold and en not in seen:
            if args.types and (r["rec_type"] or "") not in args.types.split(","):
                continue
            seen.add(en)
            rows.append(r)
    random.Random(7).shuffle(rows)
    rows = rows[:args.n]
    print(f"строк корпуса с известным ответом: {len(rows)}\n", file=out)

    dlg = DLG.load(MODS, GAME)
    spk = SP.load(MODS, GAME, repo=None)
    style_ex = OC.build_examples(repo)
    parts = tuple(x.strip() for x in args.parts.split(",") if x.strip())
    print(f"части контекста: {', '.join(parts)}\n", file=out)
    talk_text = {}
    for t in con.execute(
            "SELECT esp_name, form_id, original FROM strings WHERE "
            "(rec_type='DIAL' AND field_type='FULL') OR "
            "(rec_type='INFO' AND field_type='NAM1')"):
        k = f"{(t['esp_name'] or '').lower()}:{(t['form_id'] or '').upper()[-6:]}"
        talk_text.setdefault(k, t["original"] or "")

    score = {"без контекста": [0, 0, 0], "с контекстом": [0, 0, 0]}
    done = 0
    for r in rows:
        en = (r["original"] or "").strip()
        want = hold[en].strip()
        term = _build_terminology([en])
        ctx = context_for(r, dlg, spk, repo, talk_text, parts, style_ex)
        for name, use in (("без контекста", ""), ("с контекстом", ctx)):
            try:
                got = parse_numbered_output(
                    infer(args.worker, build_prompt(
                        texts=[en], src_lang="English", tgt_lang="Russian",
                        context=use, terminology=term)), 1)
                ans = (got[0] if got else "").strip()
            except Exception as exc:                               # noqa: BLE001
                print(f"  сбой: {exc}", file=out, flush=True)
                ans = ""
            s = score[name]
            s[0] += ans == want
            s[1] += loose(ans) == loose(want)
            s[2] += by_lemma(want, ans)
        done += 1
        if done % 20 == 0:
            print(f"  {done}/{len(rows)}…", file=out, flush=True)

    print(f"\n{'ветка':<16}{'точно':>9}{'без огрехов':>14}{'по леммам':>12}", file=out)
    for name, s in score.items():
        n = max(done, 1)
        print(f"{name:<16}{100.0*s[0]/n:>8.1f}%{100.0*s[1]/n:>13.1f}%"
              f"{100.0*s[2]/n:>11.1f}%", file=out)


if __name__ == "__main__":
    main()
