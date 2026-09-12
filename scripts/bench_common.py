"""Asking a worker one question, and reading the answer out of what comes back.

Shared by the benches. The raw inference chunk gets no chat template, so the model
reasons out loud: a <think> block, an analysis, often a markdown table of alternatives,
and the answer last. The first version of term_bench took "the first non-empty line",
got "<think>" every time, and scored three prompt variants at 0% — a measurement that
said nothing about any of them and looked like a result.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

MASTER = "http://127.0.0.1:5000"

_THINK = re.compile(r"<think>.*?(?:</think>|$)", re.S)
_CYR = re.compile(r"[А-Яа-яЁё]")
# Chat-template control tokens. A prompt without the template does not end the turn, so
# the model runs straight past its answer into a new one and «Друидские обмотки» came
# back as «Друидские обмотки<|endoftext|><|im_start|>system». Everything from the first
# of these onward is the next turn, not the answer.
_SPECIAL = re.compile(r"<\|(?:endoftext|im_start|im_end|eot_id)\|>")

# Answer on one line and nothing else; /no_think turns Qwen's reasoning off where the
# template would otherwise switch it on.
TERSE = "Answer with the Russian translation on a single line and nothing else. /no_think"


def infer(label: str, prompt: str, timeout: int = 240) -> str:
    """One prompt on one worker. Raises on a busy or model-less worker rather than
    returning something that looks like an answer."""
    body = json.dumps({"prompt": prompt, "timeout": timeout}).encode()
    req = urllib.request.Request(f"{MASTER}/api/workers/{label}/infer", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout + 30) as r:
        return (json.load(r) or {}).get("result") or ""


def clean_output(raw: str) -> str:
    """The answer, out of everything the model says around it.

    Cut at the first control token — what follows is the model starting a new turn, not
    more of the answer. Then drop the <think> block and take the LAST line carrying
    Cyrillic that is not a table row, a heading or a "Label: value" line, because the
    conclusion comes after the working.
    """
    text = _SPECIAL.split(raw or "", 1)[0]
    text = _THINK.sub(" ", text)
    lines = [ln.strip().strip("*").strip() for ln in text.splitlines()]
    lines = [ln.strip('"').strip("«»").strip() for ln in lines if ln.strip()]
    cyr = [ln for ln in lines
           if _CYR.search(ln) and not ln.startswith(("|", "#", "-", ">"))
           and len(ln) < 400 and ":" not in ln[:14]]
    return cyr[-1] if cyr else (lines[-1] if lines else "")


def translate_blind(label: str, english: str, timeout: int = 240) -> str:
    """One unaided translation, through the same prompt builder production uses.

    Hand-rolling the prompt produced two faults that had nothing to do with the question
    being asked: with no chat template the model ran past its answer into a new turn, and
    with no system prompt it added content — "Head to Morvunskar" came back as
    «Отправляйтесь в Морвунскару и поговорите с Ярлом», a Jarl the source never mentions.
    The builder wraps ChatML and carries the rules; the answer comes back as the numbered
    list every translation returns.

    No glossary, no context, no stored answer: the two translators in an ensemble share
    nothing but the source, or their agreement means nothing.
    """
    import sys
    from pathlib import Path
    rw = Path(__file__).resolve().parents[1] / "remote_worker"
    if str(rw) not in sys.path:
        sys.path.insert(0, str(rw))
    from prompt.builder import build_prompt
    from prompt.parser import parse_numbered_output

    raw = infer(label, build_prompt([english], "English", "Russian"), timeout)
    got = parse_numbered_output(_SPECIAL.split(raw or "", 1)[0], 1)
    if got and got[0].strip():
        return got[0].strip()
    return clean_output(raw)


def workers_free() -> list[str]:
    """Labels of workers that are alive, hold a model, and have nothing to do.

    A busy worker refuses inference with a 409, and a bench that treats the refusal as an
    answer measures nothing. `idle_starved` is the agent's own report that it asked for
    work and got none — which is the only reliable signal here, because the registry
    keeps package rows that were superseded and never started, and judging by
    done < total counts those as work in progress forever.
    """
    try:
        with urllib.request.urlopen(f"{MASTER}/api/workers", timeout=60) as r:
            ws = json.load(r)
    except Exception:
        return []
    ws = ws if isinstance(ws, list) else ws.get("workers", [])
    return [w["label"] for w in ws
            if w.get("alive") and (w.get("model") or "").strip()
            and (w.get("health") or {}).get("idle_starved")]
