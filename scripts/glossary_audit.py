"""Какие записи глоссария не нарушаются, а просто двусмысленны.

Термин с высокой долей нарушений — это почти всегда не испорченный корпус, а плохая
запись. «Keep» в английском ещё и глагол, и требовать «Крепость» в «keep the sword»
бессмысленно; «Hide» — и шкура, и прятаться.

Доля считается по корпусу и распределение снова оказывается двугорбым:

    Keep       87%  (2 681 из 3 082)   ← двусмысленно
    Hide       60%  (473 из 787)       ← двусмысленно
    Innkeeper  21%
    Draugr     14%                     ← настоящий дрейф
    всё остальное  <= 1%

Между 21% и 60% пусто, поэтому порог не оценочное суждение. Запускать после любого
пополнения глоссария: одна плохая запись даёт тысячи ложных срабатываний и стоит
машинных часов на их «исправление».

    python scripts/glossary_audit.py
"""
import io
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import translator.validation.terminology as T  # noqa: E402

out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

AMBIGUOUS_RATE = 0.40      # выше — запись двусмысленна, а не нарушена
MIN_OCCURRENCES = 30       # ниже — доля это шум


def audit(rows, terms) -> list[tuple]:
    """[(доля, нарушений, вхождений, термин, требуемое)] по убыванию доли."""
    result = []
    for en, value in terms.items():
        forms = T.accepted_forms(value)
        if not any(T._is_enforceable(f) for f in forms):
            continue
        one = {en: value}
        hit = bad = 0
        for original, translation in rows:
            if not T._contains_word(T._strip_tokens(original), en):
                continue
            hit += 1
            if T.glossary_violations(original, translation, one):
                bad += 1
        if hit >= MIN_OCCURRENCES:
            result.append((bad / hit, bad, hit, en, T.canonical(value)))
    result.sort(reverse=True)
    return result


def main() -> None:
    path = (ROOT / "cache" / "translations.db").as_posix()
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT original, translation FROM strings WHERE status='translated' "
        "AND TRIM(COALESCE(translation,'')) <> ''").fetchall()
    con.close()
    terms = json.loads((ROOT / "data" / "skyrim_terms.json").read_text(encoding="utf-8"))

    result = audit(rows, terms)
    flagged = [r for r in result if r[0] >= AMBIGUOUS_RATE]
    print(f"строк: {len(rows):,}   проверяемых записей: {len(result)}", file=out)
    print(f"подозрительных (доля >= {AMBIGUOUS_RATE:.0%}): {len(flagged)}\n", file=out)
    print(f"{'доля':>6} {'наруш':>6} {'вхожд':>6}  термин", file=out)
    for rate, bad, hit, en, ru in result[:20]:
        mark = "  ← двусмысленно" if rate >= AMBIGUOUS_RATE else ""
        print(f"{rate:>6.0%} {bad:>6} {hit:>6}  {en} → {ru}{mark}", file=out)


if __name__ == "__main__":
    main()
