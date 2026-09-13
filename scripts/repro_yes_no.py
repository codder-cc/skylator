"""Воспроизвести «Yes → Нет» и найти, что именно это вызывает.

254 строки в корпусе переводят "Yes" как «Нет». Это одна ошибка модели, размноженная
дедупом, — но вопрос не в размножении. Вопрос в том, почему модель так ответила: три
буквы, никакого контекста, любой словарь справляется.

Латать последствия бессмысленно, пока причина в тракте генерации жива. Поэтому здесь не
правило и не ремонт, а стенд: одна и та же строка прогоняется через ТОТ ЖЕ
build_prompt, что и продакшен, в разных условиях, и считается, как часто ответ портится.

Проверяются по одному:

    A  «Yes» в одиночку
    B  «Yes» и «No» вдвоём
    C  настоящий батч записи: предупреждение, заголовок, Yes, No
    D  то же при temperature 0
    E  то же без repetition_penalty
    F  то же без top_k

Машина должна быть свободна: агент отказывает в инференсе, пока идёт офлайн-пакет.

    python scripts/repro_yes_no.py <label> [повторов]
"""
import collections
import io
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "remote_worker"))

from prompt.builder import build_prompt          # noqa: E402
from prompt.parser import parse_numbered_output  # noqa: E402

out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
MASTER = "http://127.0.0.1:5000"

# Настоящая запись, где ошибка произошла впервые: Apocalypse, MESG 03089712.
WARNING = ("This will remove all spell tomes, scrolls and spells added by Apocalypse "
           "from the game. Are you sure?")
TITLE = "Repopulate Leveled Lists"

CASES = {
    "A · один Yes":            (["Yes"], {}),
    "B · Yes и No":            (["Yes", "No"], {}),
    "C · настоящий батч":      ([WARNING, TITLE, "Yes", "No"], {}),
    "D · батч, temp 0":        ([WARNING, TITLE, "Yes", "No"], {"temperature": 0.0}),
    "E · батч, без rep_pen":   ([WARNING, TITLE, "Yes", "No"], {"repetition_penalty": 1.0}),
    "F · батч, без top_k":     ([WARNING, TITLE, "Yes", "No"], {"top_k": 0}),
    "G · Yes и No, temp 0":    (["Yes", "No"], {"temperature": 0.0}),
}

BASE = {"temperature": 0.3, "top_k": 20, "top_p": 0.9,
        "repetition_penalty": 1.05, "max_tokens": 2048, "thinking": False}


def infer(label: str, prompt: str, params: dict, timeout: int = 300) -> str:
    body = json.dumps({"prompt": prompt, "timeout": timeout, "params": params}).encode()
    req = urllib.request.Request(f"{MASTER}/api/workers/{label}/infer", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout + 30) as r:
        payload = json.load(r)
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error") or "нет ответа")
    return payload.get("result") or ""


def main() -> None:
    label = sys.argv[1]
    runs = int(sys.argv[2]) if len(sys.argv) > 2 else 6

    for name, (texts, override) in CASES.items():
        params = dict(BASE)
        params.update(override)
        prompt = build_prompt(texts, "English", "Russian")
        idx = texts.index("Yes")
        answers = collections.Counter()
        raw_bad = None
        for _ in range(runs):
            try:
                raw = infer(label, prompt, params)
            except Exception as exc:
                print(f"{name}: машина отказала — {exc}", file=out)
                return
            got = parse_numbered_output(raw, len(texts))
            ans = (got[idx] if idx < len(got) else "").strip()
            answers[ans or "(пусто)"] += 1
            if ans and ans != "Да" and raw_bad is None:
                raw_bad = raw
        spread = "  ".join(f"{a!r}×{c}" for a, c in answers.most_common())
        bad = runs - answers.get("Да", 0)
        print(f"{name:<24} неверно {bad}/{runs}   {spread}", file=out)
        if raw_bad:
            print(f"    сырой ответ: {raw_bad[:200]!r}", file=out)


if __name__ == "__main__":
    main()
