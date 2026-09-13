"""Measure which English words this collection translates, and write that vocabulary.

The question "is this Latin word in a Russian string a leftover or a name" already has
an answer in the data. A word the collection translates 1 138 times and leaves standing
four times is a leftover those four times; a word it never translates — DLC, MCM, III,
MageFur, voice-type ids — is a name, and leaving it is correct.

So the measurement is: for every Latin word in a source whose translation came out
Russian, how often is the word still there afterwards. Case-insensitively, because «AoE»
and «aoe» are one word and counting them apart made an acronym look like a leftover.

The answer is bimodal — almost everything is translated almost always, a few dozen names
are kept almost always, and the middle is nearly empty. That is why the threshold can be
drawn at 30% without it being a judgement call.

Writes data/translated_vocabulary.txt, which quality.untranslated_word_violations reads.
Re-run when the collection changes: the file is a measurement of this pack, not a
dictionary of English.

    python scripts/build_vocabulary.py            # measure and report
    python scripts/build_vocabulary.py --write    # …and rewrite the shipped file
"""
import collections
import io
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from translator.validation.quality import _FORMAT_TAG_RE, _INLINE_TOKEN_RE  # noqa: E402

DB = ROOT / "cache" / "translations.db"
OUT = ROOT / "data" / "translated_vocabulary.txt"

MIN_SEEN = 5       # below this the rate is noise
MAX_RATE = 0.30    # survives more often than this → the pack keeps it on purpose

WORD = re.compile(r"(?<![A-Za-z'’])[A-Za-z]{3,}(?![A-Za-z])")
CYRILLIC = re.compile(r"[А-яЁё]")

out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def bare(text):
    """Drop game tokens and format tags — they are not words either side."""
    return _INLINE_TOKEN_RE.sub(" ", _FORMAT_TAG_RE.sub(" ", text or ""))


def measure():
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    con.execute("PRAGMA cache_size=-400000")
    seen = collections.Counter()     # sources containing the word
    kept = collections.Counter()     # …whose translation still contains it
    rows = 0
    for en, ru in con.execute(
        "SELECT original, translation FROM strings "
        "WHERE status='translated' AND TRIM(translation) <> '' AND translation <> original"
    ):
        russian = bare(ru)
        if not CYRILLIC.search(russian):
            continue                 # not a Russian translation; tells us nothing
        rows += 1
        words = {w.lower() for w in WORD.findall(bare(en))}
        if not words:
            continue
        low = russian.lower()
        for w in words:
            seen[w] += 1
            if re.search(rf"(?<![A-Za-z]){re.escape(w)}(?![A-Za-z])", low):
                kept[w] += 1
    con.close()
    return rows, seen, kept


def main(write):
    rows, seen, kept = measure()
    print(f"строк учтено: {rows}   различных слов: {len(seen)}", file=out)

    table = sorted(((w, seen[w], kept[w], kept[w] / seen[w])
                    for w in seen if seen[w] >= MIN_SEEN), key=lambda r: r[3])
    print(f"слов, встреченных >= {MIN_SEEN} раз: {len(table)}\n", file=out)

    for lo, hi in [(0.0, .02), (.02, .1), (.1, .3), (.3, .7), (.7, .95), (.95, 1.01)]:
        n = sum(1 for r in table if lo <= r[3] < hi)
        print(f"  выживаемость {lo:>4.2f}–{hi:<4.2f}  {n:>6} слов", file=out)

    print("\nпочти никогда не выживают (перевод обязателен):", file=out)
    for w, s, k, rate in table[:10]:
        print(f"   {w:<16} источник {s:>5}, осталось {k:>4}  ({rate:.1%})", file=out)
    print("\nвсегда выживают (имя — оставлять правильно):", file=out)
    for w, s, k, rate in [r for r in table if r[3] >= .95][:14]:
        print(f"   {w:<16} источник {s:>5}, осталось {k:>4}  ({rate:.1%})", file=out)
    middle = [r for r in table if .3 <= r[3] < .7]
    print(f"\nсередина, {len(middle)} слов — здесь порог и проходит:", file=out)
    for w, s, k, rate in middle[:8]:
        print(f"   {w:<16} источник {s:>5}, осталось {k:>4}  ({rate:.1%})", file=out)

    words = sorted(w for w, _s, _k, rate in table if rate <= MAX_RATE)
    names = sorted(w for w, _s, _k, rate in table if rate > MAX_RATE)
    if not write:
        print(f"\n(--write запишет {len(words)} слов в {OUT.relative_to(ROOT)})", file=out)
        return

    bands = {f"{lo:.2f}": sum(1 for r in table if r[3] < lo) for lo in (.02, .3)}
    header = (
        "# English words this collection translates, so one of them left standing in a\n"
        "# Russian string is a leftover rather than a name.\n"
        "#\n"
        "# Measured, not curated — scripts/build_vocabulary.py. For every Latin word in a\n"
        "# source whose translation is Russian, how often the word is still there after.\n"
        f"# Of the {len(table)} words seen {MIN_SEEN}+ times, {bands['0.02']} survive under 2% of the\n"
        f"# time and {sum(1 for r in table if r[3] >= .95)} survive over 95% — MCM, DLC, III, MageFur, voice-type\n"
        f"# ids. Only {len(middle)} lie between 30% and 70%, so the threshold below is drawn\n"
        "# through empty space rather than through a judgement call.\n"
        "#\n"
        f"# words: {len(words)}   threshold: survives <= {MAX_RATE:.0%} of the time,"
        f" seen >= {MIN_SEEN} times\n"
        f"# kept out as names/acronyms ({len(names)}): {', '.join(names[:24])}…\n"
    )
    OUT.write_text(header + "\n".join(words) + "\n", encoding="utf-8")
    print(f"\nзаписано слов: {len(words)}   размер: {OUT.stat().st_size / 1024:.0f} КБ",
          file=out)
    print(f"исключено как имена ({len(names)}): {', '.join(names[:30])}", file=out)


if __name__ == "__main__":
    main(write="--write" in sys.argv)
