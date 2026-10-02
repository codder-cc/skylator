"""Сравнение моделей перевода на этом ПК (RTX 5080), без боевых агентов.

    python scripts/mt_bench.py --model hymt2 --mode plain --set H
    python scripts/mt_bench.py --model hymt2 --mode ctx   --set D
    python scripts/mt_bench.py --model qwen27 --mode plain --set H

Каждая модель получает СВОЙ шаблон запроса: сравнение не должно мерить ошибку
подключения. Режимы:

  plain  только строка — то, ради чего модель обучена;
  ctx    плюс наши подсказки, которые модель умеет принимать: официальные имена и
         похожие записи игры (как в боевом профиле, без отложенной выборки), пол
         говорящего и обращение на «ты». Для моделей без инструкций (MiLMMT,
         TranslateGemma) этот режим не определён.

Меры — те же, что у стенда (scripts/bench.py): на H — доля совпадений с официальной
локализацией, на D — chrF к эталону, имена и род. Брак — по правилам корпуса.
Ответы — logs/experiments/mt_<model>_<mode>_<set>.json.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

AI = "D:/DevSpace/AI"
MODELS = {
    "hymt2":  {"path": f"{AI}/mt_bench/HY-MT2-7B-Q8_0.gguf", "fmt": "hymt", "layers": -1,
               "sampling": {"temperature": 0.0, "top_k": 1, "repeat_penalty": 1.05}},
    "milmmt": {"path": f"{AI}/mt_bench/MiLMMT-46-12B-v1.0.Q6_K.gguf", "fmt": "milmmt",
               "layers": -1, "sampling": {"temperature": 0.0, "top_k": 1}},
    "tgemma": {"path": f"{AI}/mt_bench/translategemma-12b-it.Q6_K.gguf", "fmt": "tgemma",
               "layers": -1, "sampling": {"temperature": 0.0, "top_k": 1}},
    "qwen27": {"path": f"{AI}/Huihui-Qwen3.5-27B-Claude-4.6-Opus-abliterated-Q4_K_M.gguf/"
                       "Huihui-Qwen3.5-27B-Claude-4.6-Opus-abliterated-Q4_K_M.gguf",
               "fmt": "qwen", "layers": 48,
               "sampling": {"temperature": 0.0, "top_k": 1, "repeat_penalty": 1.05}},
}

QWEN_SYSTEM = ("You are a professional video game translator specializing in The Elder "
               "Scrolls V: Skyrim. You produce complete, accurate, natural-sounding Russian "
               "translations that fit Skyrim's lore and UI conventions.")


def hints_for(item: dict, set_name: str) -> list[str]:
    """Подсказки ctx-режима — из тех же источников, что у боевого профиля."""
    from translator.validation import official_context as oc
    en = item["en"]
    h = []
    for en_name, ru in oc.entities_in(en):
        h.append(f"{en_name} = {ru}")
    for en_a, ru_a in oc.analogs_in(en, item.get("rec_type") or ""):
        h.append(f"{en_a} = {ru_a}")
    return h


def speaker_note(item: dict) -> str:
    g = item.get("gender")
    return {"m": "The speaker is male.", "f": "The speaker is female."}.get(g, "")


def build(fmt: str, text: str, hints: list[str], note: str, is_dialogue: bool) -> tuple[str, list]:
    """(сырой промпт, стоп-последовательности) в родном шаблоне модели."""
    if fmt == "hymt":
        instr = ""
        if hints:
            instr += ("Refer to the following translations:\n"
                      + "\n".join(f"{x.split(' = ')[0]} translates to {x.split(' = ', 1)[1]}"
                                  for x in hints if " = " in x) + "\n\n")
        if is_dialogue:
            # Отдельным блоком ДО инструкции: внутри неё модель переводила подсказку
            # вместе с репликой («Это фрагмент устного диалога из Skyrim…»).
            instr = ("Context: this is a line of spoken dialogue from a fantasy game. "
                     + note + " The listener is addressed informally (ты)."
                     + "\n\n" + instr)
        user = (instr + "Translate the following text into Russian. Note that you should "
                "only output the translated result without any additional explanation."
                + "\n\n" + text)
        # Шаблон из метаданных GGUF (tokenizer.chat_template): без системного
        # сообщения — <|startoftext|>{user}<|extra_0|>, ответ кончается <|eos|>.
        return (f"<|startoftext|>{user}<|extra_0|>", ["<|eos|>"])
    if fmt == "milmmt":
        return (f"Translate this from English to Russian:\nEnglish: {text}\nRussian:", ["\nEnglish:"])
    if fmt == "tgemma":
        user = ("You are a professional English (en) to Russian (ru) translator. Your goal is "
                "to accurately convey the meaning and nuances of the original English text "
                "while adhering to Russian grammar, vocabulary, and cultural sensitivities.\n"
                "Produce only the Russian translation, without any additional explanations or "
                "commentary. Please translate the following English text into Russian:\n\n\n"
                + text)
        return (f"<start_of_turn>user\n{user}<end_of_turn>\n<start_of_turn>model\n",
                ["<end_of_turn>"])
    if fmt == "qwen":
        rules = ("Translate the string from English to Russian. Translate the complete text. "
                 "Preserve tokens like <Alias=...>, <mag>, %d exactly. Address the player as "
                 "«ты». Output only the translation.")
        ctx = ""
        if hints:
            ctx += ("\nNames and official entries of the game — use these renderings: "
                    + "; ".join(hints))
        if note:
            ctx += "\n" + note + " Use the speaker's gender for first-person forms."
        user = f"{rules}{ctx}\n\nString:\n{text}"
        return (f"<|im_start|>system\n{QWEN_SYSTEM}<|im_end|>\n<|im_start|>user\n{user}"
                f"<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n", ["<|im_end|>"])
    raise ValueError(fmt)


def build_hymt2(item: dict, note: str, is_dialogue: bool) -> tuple[str, list]:
    """Hy-MT2 с разделением: имена — терминология («X translates to Y» значит «замени»),
    похожие записи игры — только образец оборота. В ctx оба шли терминологией, и модель
    переписывала чужую запись целиком: «Balmora Blue» → «Записка о Балморской сини»."""
    from translator.validation import official_context as oc
    en = item["en"]
    terms = oc.entities_in(en)
    analogs = oc.analogs_in(en, item.get("rec_type") or "")
    head = ""
    if terms:
        head += ("Refer to the following translations:\n"
                 + "\n".join(f"{a} translates to {b}" for a, b in terms) + "\n\n")
    if analogs:
        head += ("For reference, this is how the official Russian version of the game words "
                 "similar entries (follow the wording pattern, do not copy them): "
                 + "; ".join(f"{a} = {b}" for a, b in analogs) + "\n\n")
    if is_dialogue:
        head = ("Context: this is a line of spoken dialogue from a fantasy game. " + note
                + " The listener is addressed informally (ты)." + "\n\n" + head)
    user = (head + "Translate the following text into Russian. Note that you should only "
            "output the translated result without any additional explanation."
            + "\n\n" + en)
    return (f"<|startoftext|>{user}<|extra_0|>", ["<|eos|>"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=sorted(MODELS), required=True)
    ap.add_argument("--mode", choices=("plain", "ctx", "ctx2"), default="plain")
    ap.add_argument("--set", choices=("H", "D"), required=True)
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()
    m = MODELS[args.model]
    if args.mode != "plain" and m["fmt"] in ("milmmt", "tgemma"):
        sys.exit("эта модель инструкций не принимает — ctx-режим для неё не определён")
    import bench as B
    from llama_cpp import Llama
    from translator.validation.quality import compute_string_status
    items = B._load_set(args.set)[:args.limit] if args.limit else B._load_set(args.set)
    llm = Llama(model_path=m["path"], n_gpu_layers=m["layers"], n_ctx=4096, verbose=False)
    ans, t0 = {}, time.time()
    for i, it in enumerate(items, 1):
        hints = hints_for(it, args.set) if args.mode == "ctx" else []
        note = speaker_note(it) if args.mode != "plain" else ""
        if args.mode == "ctx2":
            prompt, stop = build_hymt2(it, note, args.set == "D")
        else:
            prompt, stop = build(m["fmt"], it["en"], hints, note, args.set == "D")
        r = llm(prompt, max_tokens=512, stop=stop, **m["sampling"])
        t = (r["choices"][0]["text"] or "").strip()
        fr = r["choices"][0].get("finish_reason")
        _q, _t, _iss, st = compute_string_status(it["en"], t, None, it.get("rec_type"),
                                                 it.get("field_type"))
        ans[it["id"]] = {"translation": t, "rules_status": st, "finish_reason": fr}
        if i % 50 == 0:
            print(f"  {i}/{len(items)}  {time.time() - t0:.0f} с", file=out, flush=True)
    secs = time.time() - t0
    score = (B.score_H(items, ans) if args.set == "H" else B.score_D(items, ans))["per"]
    per = list(score.values())
    summ = {"n": len(per), "sec_per_line": round(secs / len(items), 2),
            "defect": sum(p["defect"] for p in per)}
    if args.set == "H":
        summ["clean"] = round(100 * sum(p["clean"] for p in per) / len(per), 1)
        summ["exact"] = round(100 * sum(p["exact"] for p in per) / len(per), 1)
    else:
        summ["chrf"] = round(sum(p["chrf"] for p in per) / len(per), 2)
        g = [p["gender_ok"] for p in per if p["gender_ok"] is not None]
        nm = [p["name_ok"] for p in per if p["name_ok"] is not None]
        summ["gender_ok"] = f"{sum(g)}/{len(g)}"
        summ["name_ok"] = f"{sum(nm)}/{len(nm)}"
    name = f"mt_{args.model}_{args.mode}_{args.set}"
    (ROOT / "logs" / "experiments" / f"{name}.json").write_text(json.dumps(
        {"name": name, "summary": summ, "answers": ans}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print(f"{name}: {summ}", file=out)


if __name__ == "__main__":
    main()
