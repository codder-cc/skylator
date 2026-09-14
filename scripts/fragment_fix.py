"""Чинить в книге испорченный абзац, а не всю книгу.

Разбор помеченных книг длиннее 6 000 знаков: из 390 только 137 действительно не хватает
текста (обрыв, потерянная разметка) — их надо переводить заново. Остальным 253 нужна
правка отдельных слов:

    книга 9 710 знаков, 97 абзацев
    претензия: mixed alphabets in: Скарabei
    претензия: untranslated English: souljewel, myriad

Переводить ради этого всю книгу — 4 000 токенов вывода вместо 200, и главное: остальные
96 абзацев хорошего текста переписываются заново без всякой нужды. Замер такой замены на
принятых строках давал 30% переписанного впустую, и на книге эта цена платится целиком.

Поэтому сюда идёт только абзац с дефектом. Модель видит, какие слова испорчены, и
получает единственное задание — заменить их. Остальной текст книги не показывается и не
трогается: он склеивается обратно байт в байт.

Результат принимается не на слово модели, а по суду: претензий должно стать МЕНЬШЕ и ни
одной новой. Иначе абзац остаётся как был.

ЧЕГО ОН СЕЙЧАС НЕ МОЖЕТ, и это не замысел, а транспорт.

Точечной правке нужен одиночный запрос к модели, а такого пути к парку сегодня нет:

    5080 — единственная машина, до которой отсюда есть прямой доступ, но её сборка
    llama.cpp на одиночном /infer падает в CUDA-пуле:
        GGML_ASSERT(ptr == (void *)((char *)(pool_addr) + pool_used)) failed
    Офлайн-пакеты этот путь обходят и работают весь день, одиночный запрос — нет.
    Проверено дважды, оба раза агент умирал посреди работы.

    Mac'и живут на другой подсети (192.168.3.x) и отсюда недоступны вовсе: они сами
    ходят к мастеру, обратной связи нет.

Правильное решение — своя область раздачи в мастере, где единицей работы будет абзац, а
не строка: мастер собирает пакет из абзацев, агент переводит их обычным (рабочим) путём,
хост склеивает обратно. Это заметная работа, и она стоит примерно шести часов машинного
времени, которые сейчас уходят на переперевод книг целиком. Пока цена не сошлась.

До тех пор скрипт готов и проверен на разборе: он умеет находить испорченные слова,
абзац вокруг них и судить результат. Не хватает только того, кому задать вопрос.

    python scripts/fragment_fix.py --agent http://127.0.0.1:8765          # сухой прогон
    python scripts/fragment_fix.py --agent http://127.0.0.1:8765 --write
    python scripts/fragment_fix.py --min-chars 0 --limit 50 --write       # не только книги
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
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from translator.validation.quality import compute_string_status  # noqa: E402
from translator.validation.terminology import load_terms  # noqa: E402

out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# Претензии, которые лечатся заменой слов. Всё остальное — обрыв, потерянная разметка,
# пропавший токен — означает, что текста НЕ ХВАТАЕТ, и абзацем это не чинится.
WORD_LEVEL = ("untranslated English", "untranslated word", "mixed alphabets",
              "foreign script", "glossary")
_WORDS_RE = re.compile(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё'’-]{2,}")
# Абзацем считается кусок между переводами строки: книги Skyrim так и устроены, а
# [pagebreak] и <p align> стоят на своих строках.
_SPLIT_RE = re.compile(r"(\r?\n)")


def offending_words(issues: list[str]) -> list[str]:
    """Слова, названные в претензиях. Только они и подлежат замене."""
    found: list[str] = []
    for issue in issues:
        if not issue.startswith(WORD_LEVEL):
            continue
        tail = issue.split(":", 1)[1] if ":" in issue else ""
        if issue.startswith("glossary"):
            continue           # у глоссария в хвосте требование, а не испорченное слово
        for w in _WORDS_RE.findall(tail):
            if w.lower() not in ("should", "be", "in", "the"):
                found.append(w)
    # порядок сохраняем, повторы убираем
    seen: set = set()
    return [w for w in found if not (w.lower() in seen or seen.add(w.lower()))]


def paragraphs(text: str) -> list[str]:
    """Куски текста и разделители между ними — склейка обязана быть побайтовой."""
    return _SPLIT_RE.split(text or "")


def locate(parts: list[str], words: list[str]) -> dict[int, list[str]]:
    """Индекс абзаца → какие из испорченных слов в нём стоят."""
    hit: dict[int, list[str]] = collections.defaultdict(list)
    for i, part in enumerate(parts):
        if not part.strip():
            continue
        for w in words:
            if re.search(r"(?<![A-Za-zА-Яа-яЁё])" + re.escape(w)
                         + r"(?![A-Za-zА-Яа-яЁё])", part):
                hit[i].append(w)
    return hit


PROMPT = """You are fixing a Russian translation of an Elder Scrolls book.

The paragraph below is already translated and correct except for these words, which \
were left in English or half-transliterated: {words}

Rewrite ONLY those words in proper Russian, declined as the sentence requires. \
Every other word, every space, every markup tag and every line must stay exactly as it \
is. Do not translate the paragraph again, do not improve the style, do not add or \
remove anything.

Output the corrected paragraph and nothing else — no explanation, no quotes, no label.

Paragraph:
{paragraph}"""


SYSTEM = ("You are a meticulous editor of Russian translations for The Elder Scrolls V: "
          "Skyrim. You change exactly what you are told to change and nothing else.")


def chatml(system: str, user: str) -> str:
    """Та же обёртка, что строит промпты агенту: ChatML, без размышления."""
    nl = "\n"
    return (f"<|im_start|>system{nl}{system}<|im_end|>{nl}"
            f"<|im_start|>user{nl}{user}<|im_end|>{nl}"
            f"<|im_start|>assistant{nl}")


def ask(agent: str, prompt: str, timeout: int = 300) -> str:
    # /chat у агента сломан — он передаёт температуру туда, где бэкенд ждёт объект
    # параметров, и падает на `float has no attribute max_tokens`. /infer берёт готовый
    # промпт и словарь параметров, поэтому ChatML собирается здесь.
    req = urllib.request.Request(
        agent.rstrip("/") + "/infer",
        data=json.dumps({"prompt": chatml(SYSTEM, prompt),
                         "params": {"temperature": 0.1, "max_tokens": 1536}}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    job_id = json.load(urllib.request.urlopen(req, timeout=60))["job_id"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(2)
        d = json.load(urllib.request.urlopen(
            f"{agent.rstrip('/')}/jobs/{job_id}", timeout=60))
        if d.get("status") == "done":
            return (d.get("result") or "").strip()
        if d.get("status") == "error":
            raise RuntimeError(d.get("error") or "agent error")
    raise TimeoutError("agent did not answer in time")


def agent_is_free(agent: str, master: str, label: str | None = None) -> tuple[bool, str]:
    """Свободна ли машина. Занятую трогать нельзя — она падает.

    Прямой запрос к /infer на агенте, который в это же время считает офлайн-пакет, кладёт
    процесс: два потока входят в одну модель llama.cpp и ловят access violation. Проверено
    ценой упавшего агента посреди работы, поэтому проверка стоит здесь, а не в советах.
    """
    try:
        health = json.load(urllib.request.urlopen(agent.rstrip("/") + "/health", timeout=15))
    except Exception as exc:
        return False, f"агент не отвечает: {exc}"
    if not health.get("model_loaded"):
        return False, "у агента не загружена модель"
    if health.get("queue_depth"):
        return False, f"у агента в очереди {health['queue_depth']} задач"
    try:
        workers = json.load(urllib.request.urlopen(master.rstrip("/") + "/api/workers",
                                                   timeout=30))
    except Exception:
        return True, "мастер недоступен — сужу по самому агенту"
    for w in workers:
        if label and w.get("label") != label:
            continue
        jobs = w.get("offline_jobs") or []
        if jobs and (w.get("health") or {}).get("open_assignments"):
            return False, (f"{w['label']} считает пакет "
                           f"{sum(x.get('done', 0) for x in jobs)}/"
                           f"{sum(x.get('total', 0) for x in jobs)}")
    return True, "свободна"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default="http://127.0.0.1:8765")
    ap.add_argument("--master", default="http://127.0.0.1:5000")
    ap.add_argument("--label", default="windows-DeadLine",
                    help="метка этой машины у мастера — чтобы проверить, не занята ли она")
    ap.add_argument("--min-chars", type=int, default=6000)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="не проверять занятость (уронит агента, если он считает пакет)")
    args = ap.parse_args()

    free, why = agent_is_free(args.agent, args.master, args.label)
    if not free and not args.force:
        print(f"машина занята: {why}", file=out)
        print("дождитесь конца пакета или снимите его — прямой запрос к занятому агенту "
              "роняет процесс", file=out)
        return
    print(f"машина: {why}", file=out)

    terms = load_terms()
    con = sqlite3.connect(str(ROOT / "cache" / "translations.db"), timeout=180)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=180000")
    con.execute("PRAGMA cache_size=-400000")

    rows = con.execute(
        "SELECT id, original, translation, rec_type, field_type FROM strings "
        "WHERE status='needs_review' AND LENGTH(original) >= ? "
        "AND TRIM(COALESCE(translation,'')) <> ''", (args.min_chars,)).fetchall()
    print(f"помеченных строк от {args.min_chars} знаков: {len(rows):,}", file=out)

    stat: collections.Counter = collections.Counter()
    done = 0
    now = time.time()
    for r in rows:
        if args.limit and done >= args.limit:
            break
        original, stored = r["original"], r["translation"]
        _q, _t, issues, _s = compute_string_status(
            original, stored, terms, r["rec_type"], r["field_type"])
        if any(not i.startswith(WORD_LEVEL) for i in issues):
            stat["не хватает текста — переводить заново"] += 1
            continue
        words = offending_words(issues)
        if not words:
            stat["слова не названы"] += 1
            continue
        parts = paragraphs(stored)
        hits = locate(parts, words)
        if not hits:
            stat["слово в тексте не найдено"] += 1
            continue

        fixed_parts = list(parts)
        touched = 0
        for idx, ws in hits.items():
            try:
                answer = ask(args.agent, PROMPT.format(words=", ".join(ws),
                                                       paragraph=parts[idx]))
            except Exception as exc:
                print(f"   агент не ответил: {exc}", file=out)
                continue
            # Ответ обязан остаться абзацем, а не превратиться в пересказ: длина в
            # полтора раза больше или меньше — это уже не правка слов.
            if not answer or not 0.6 <= len(answer) / max(len(parts[idx]), 1) <= 1.6:
                stat["ответ не похож на абзац"] += 1
                continue
            fixed_parts[idx] = answer
            touched += 1
        if not touched:
            continue

        candidate = "".join(fixed_parts)
        qs, _tk, new_issues, status = compute_string_status(
            original, candidate, terms, r["rec_type"], r["field_type"])
        # Меньше претензий и ни одной новой — иначе абзац остаётся как был.
        if len(new_issues) >= len(issues) or (set(new_issues) - set(issues)):
            stat["лучше не стало — отклонено"] += 1
            continue
        stat["починено"] += 1
        done += 1
        print(f"   {len(original):>6} знаков  {len(issues)}→{len(new_issues)} претензий  "
              f"[{status}]  слова: {', '.join(words[:4])}", file=out)
        if args.write:
            con.execute(
                "INSERT INTO string_history (string_id, translation, status, source, "
                "created_at) VALUES (?,?,?,?,?)",
                (r["id"], stored, status, "fragment:fix", now))
            con.execute(
                "UPDATE strings SET translation=?, status=?, quality_score=?, "
                "updated_at=? WHERE id=?", (candidate, status, qs, now, r["id"]))
            con.commit()

    print(file=out)
    for k, v in stat.most_common():
        print(f"  {k:<40}{v:>6}", file=out)
    print(f"\n{'ЗАПИСАНО' if args.write else 'сухой прогон'}", file=out)


if __name__ == "__main__":
    main()
