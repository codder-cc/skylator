"""Что меняет контекст: один и тот же текст, два промпта, ответы рядом.

Вопрос, на который это отвечает, стоил спора: помогает ли модели знание о разговоре,
или довольно самой строки. Проверяется единственным честным способом — одна и та же
модель, одна и та же строка, разница только в контексте:

    было    промпт без контекста вовсе. Проход по корпусу раздавал моды с пустой
            строкой в этом поле, то есть так переводился ВЕСЬ корпус
    стало   справка о моде, карточка собеседника и соседние реплики разговора

Считается на агенте, а не тут: на хосте нет модели, а сравнивать надо ту же самую.

    python scripts/context_ab.py --worker darwin-int00mac-5YVL25 --n 8
    python scripts/context_ab.py --worker <label> --like "%вино%"
"""
from __future__ import annotations

import argparse
import io
import json
import sqlite3
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "remote_worker"))

from prompt.builder import build_prompt                     # noqa: E402
from prompt.parser import parse_numbered_output             # noqa: E402
from translator.characters import dialogue as DLG           # noqa: E402
from translator.characters import speakers as SP            # noqa: E402
from translator.context import mod_summary as MS            # noqa: E402

out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
MODS = Path(r"H:\Nolvus\Instances\Nolvus Awakening\MODS\mods")
GAME = Path(r"H:\Nolvus\Instances\Nolvus Awakening\STOCK GAME\Data")
LABELS = {"answer": "the character answers:", "topic": "the player says:",
          "prev": "just before, they said:"}


class _Repo:
    def __init__(self, db):
        self.db = db


def infer(label: str, prompt: str, timeout: int = 300) -> str:
    body = json.dumps({"prompt": prompt, "timeout": timeout,
                       "params": {"temperature": 0.3, "top_k": 20, "top_p": 0.9,
                                  "repetition_penalty": 1.05, "max_tokens": 1024,
                                  "thinking": False}}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:5000/api/workers/{label}/infer", data=body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout + 30) as r:
        payload = json.load(r)
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error") or "нет ответа")
    return payload.get("result") or ""


def context_for(row, dlg, spk, repo, talk_text, blunt: bool = False) -> str:
    """Тот же порядок, что и в бою: карточка ПЕРВОЙ, затем справка о моде.

    Порядок здесь не украшение. В раздаче карточка ставится первой намеренно и не
    отбрасывается даже на коротких строках, а справка о моде — отбрасывается. Если
    опыт складывает их иначе, он меряет не то, что работает в бою.
    """
    bits = []
    ref = DLG.addressee_ref(row["esp_name"], row["form_id"], row["rec_type"],
                            row["field_type"], dlg)
    if ref:
        plug, fid = ref.split(":", 1)
        card = SP.card_for(plug, fid, spk)
        if card:
            bits.append(card.addressee_block())
            if blunt:
                sex = DLG.addressee_gender(row["esp_name"], row["form_id"], dlg)
                if sex:
                    word = "female" if sex == "f" else "male"
                    bits.append(
                        f"IMPORTANT: the listener is {word}. Every Russian word that "
                        f"describes the listener — verb, adjective AND noun — must be "
                        f"{word}. English has no gender here; Russian does, and the "
                        f"listener's gender is known.")
    else:
        block = SP.block_for(row["esp_name"], row["form_id"], spk)
        if block:
            bits.append(block)
    summary = MS.build(repo, row["mod_name"])
    if summary:
        bits.append(summary)
    talk = []
    for role, rk in DLG.neighbours(row["esp_name"], row["form_id"],
                                   row["rec_type"], row["field_type"], dlg):
        txt = (talk_text.get(rk) or "").strip()
        if txt:
            talk.append(f'  (1) {LABELS[role]} "{txt[:180]}"')
    if talk:
        bits.append("Conversation around these lines:\n" + "\n".join(talk))
    return "\n".join(bits)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker", required=True)
    ap.add_argument("--n", type=int, default=6)
    ap.add_argument("--like", help="взять строки, чей ПЕРЕВОД содержит это")
    args = ap.parse_args()

    con = sqlite3.connect(str(ROOT / "cache" / "translations.db"), timeout=180)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=180000")
    con.execute("PRAGMA cache_size=-400000")
    repo = _Repo(con)

    dlg = DLG.load(MODS, GAME)
    spk = SP.load(MODS, GAME, repo=None)
    talk_text = {}
    for t in con.execute(
            "SELECT esp_name, form_id, original FROM strings WHERE "
            "(rec_type='DIAL' AND field_type='FULL') OR "
            "(rec_type='INFO' AND field_type='NAM1')"):
        k = f"{(t['esp_name'] or '').lower()}:{(t['form_id'] or '').upper()[-6:]}"
        talk_text.setdefault(k, t["original"] or "")

    if args.like:
        sql = ("SELECT mod_name, esp_name, form_id, rec_type, field_type, original, "
               "translation FROM strings WHERE translation LIKE ? LIMIT ?")
        rows = con.execute(sql, (args.like, args.n)).fetchall()
    else:
        # Реплики игрока к персонажу с известным полом — там разница и должна быть.
        rows = [r for r in con.execute(
            "SELECT mod_name, esp_name, form_id, rec_type, field_type, original, "
            "translation FROM strings WHERE rec_type='DIAL' AND field_type='FULL' "
            "AND LENGTH(original) BETWEEN 40 AND 200 "
            "AND TRIM(COALESCE(translation,'')) <> '' LIMIT 400")
            if DLG.addressee_gender(r["esp_name"], r["form_id"], dlg)][:args.n]

    print(f"строк: {len(rows)}   машина: {args.worker}\n", file=out)
    for r in rows:
        ctx = context_for(r, dlg, spk, repo, talk_text)
        pairs = []
        blunt = context_for(r, dlg, spk, repo, talk_text, blunt=True)
        for name, use in (("без контекста", ""), ("с контекстом", ctx),
                          ("жёстче", blunt)):
            prompt = build_prompt(texts=[r["original"]], src_lang="English",
                                  tgt_lang="Russian", context=use)
            try:
                got = parse_numbered_output(infer(args.worker, prompt), 1)
                pairs.append((name, (got[0] if got else "").strip()))
            except Exception as exc:                               # noqa: BLE001
                pairs.append((name, f"<ошибка: {exc}>"))
        print(f"EN     {r['original'][:150]}", file=out)
        print(f"в базе {(r['translation'] or '')[:150]}", file=out)
        for name, got in pairs:
            print(f"{name:<14} {got[:150]}", file=out)
        if ctx:
            print("  контекст: " + ctx.replace("\n", " | ")[:200], file=out)
        print("", file=out, flush=True)


if __name__ == "__main__":
    main()
