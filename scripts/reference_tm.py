"""Чужой человеческий перевод — как справочник для сверки, а не как источник подстановки.

Замысел: взять русские версии модов, сделанные людьми, и использовать их там, где мы
сами не уверены. Не подставлять вслепую — чужой перевод тоже бывает неточным, устаревшим
или сделанным под другую версию мода, — а сверять: где мы расходимся с человеком,
строка идёт на проверку; где расходится ИМЯ, побеждает человек, потому что игрок
увидит это имя и в другом моде.

Ровно так уже работает официальная таблица Bethesda (translator/validation/authority.py),
и это единственный механизм в системе, который за день не дал ни одной ошибки:
в слоистой выборке слой vanilla дал 0 дефектов из 45 дважды.

ЧЕГО ЗДЕСЬ НЕТ И ПОЧЕМУ

Скачивания. API Nexus ссылок не даёт: 403 «You don't have permission to get download
links from the API without visiting nexusmods.com». Это не техническая помеха, а
выраженная политика сервиса, и обходить её этот скрипт не пытается. Файлы приносит
человек — менеджером модов, как обычно, — и кладёт в папку.

Поиска. Переводы на Nexus лежат отдельными страницами мода, а API v1 поиска по названию
не имеет: на выборке из сорока наших модов русский файл на странице оригинала нашёлся у
НУЛЯ. Искать приходится глазами.

ЗАЧЕМ ТОГДА ЭТО ВООБЩЕ НУЖНО

Потому что переводов нужно не 1 945, а десяток. Замер: имён, встречающихся в трёх и
более модах, — 9 491, и владеют ими единицы:

    Unofficial Skyrim SE Patch                2 295 общих имён   (nexus 266)
    Modpocalypse NPCs                         1 277             (54422)
    Weapons Armor Clothing and Clutter Fixes  1 183             (18994)
    Legacy of the Dragonborn                  1 081             (11802)
    Interesting NPCs 3DNPC                    1 019             (29194)
    Beyond Skyrim - Bruma                       616             (10917)
    Midwood Isle                                538             (28120)

Десять закачек покрывают около десяти тысяч имён, расходящихся по всему паку. Поэтому
ручной поиск здесь окупается, а обход гейта не нужен.

КАК СВЕРЯЕТСЯ

Русская версия мода — это тот же плагин с переведёнными строками, поэтому FormID у неё
те же. Ключ — (rec_type, field_type, последние шесть знаков FormID): верхний байт это
индекс плагина в порядке загрузки, свой у каждой установки.

    python scripts/reference_tm.py --drop D:/ru_mods            # отчёт о расхождениях
    python scripts/reference_tm.py --drop D:/ru_mods --dump ref.json
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.esp_engine import extract_all_strings  # noqa: E402

out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
_PLUGIN_SUFFIXES = (".esp", ".esm", ".esl")


def unpack(archive: Path, dest: Path) -> list[Path]:
    """Плагины из архива. 7z и rar распаковываются внешним 7z, который лежит у Nolvus."""
    if archive.suffix.lower() in _PLUGIN_SUFFIXES:
        return [archive]
    if archive.suffix.lower() == ".zip":
        with zipfile.ZipFile(archive) as z:
            z.extractall(dest)
    else:
        import subprocess
        seven = Path("H:/Nolvus/lib/7z.exe")
        if not seven.exists():
            print(f"   {archive.name}: нужен 7z.exe, чтобы распаковать", file=out)
            return []
        subprocess.run([str(seven), "x", "-y", f"-o{dest}", str(archive)],
                       capture_output=True, check=False)
    return [p for p in dest.rglob("*") if p.suffix.lower() in _PLUGIN_SUFFIXES]


def read_plugin(path: Path) -> dict:
    """{(rec_type, field_type, formid6): текст} из плагина."""
    got: dict = {}
    try:
        strings, _loc = extract_all_strings(path)
    except Exception as exc:
        print(f"   {path.name}: не разобрать ({exc})", file=out)
        return got
    for s in strings:
        fid = str(s.get("form_id") or "").upper()[-6:]
        if not fid:
            continue
        got[(s.get("rec_type"), s.get("field_type"), fid)] = s.get("text") or ""
    return got


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--drop", required=True,
                    help="папка, куда положены русские версии модов (архивы или плагины)")
    ap.add_argument("--dump", help="куда выгрузить справочник пар")
    args = ap.parse_args()

    drop = Path(args.drop)
    if not drop.exists():
        print(f"папки нет: {drop}", file=out)
        return

    con = sqlite3.connect(f"file:{ROOT / 'cache' / 'translations.db'}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA cache_size=-400000")

    stat: collections.Counter = collections.Counter()
    reference: dict = {}
    disagree: list = []

    files = [p for p in drop.iterdir() if p.is_file()]
    print(f"файлов в папке: {len(files)}", file=out)
    for f in files:
        with tempfile.TemporaryDirectory() as tmp:
            plugins = unpack(f, Path(tmp))
            if not plugins:
                stat["архив без плагинов"] += 1
                continue
            for pl in plugins:
                theirs = read_plugin(pl)
                if not theirs:
                    continue
                stat["плагинов прочитано"] += 1
                # Наши строки того же плагина. Имя файла у перевода то же, что у
                # оригинала: это он и есть, только с переведённым текстом.
                ours = con.execute(
                    "SELECT rec_type, field_type, form_id, original, translation, status "
                    "FROM strings WHERE LOWER(esp_name)=? "
                    "AND TRIM(COALESCE(translation,'')) <> ''",
                    (pl.name.lower(),)).fetchall()
                if not ours:
                    stat[f"плагин не наш: {pl.name}"] += 1
                    continue
                for r in ours:
                    key = (r["rec_type"], r["field_type"],
                           str(r["form_id"] or "").upper()[-6:])
                    their_text = theirs.get(key)
                    if not their_text:
                        continue
                    stat["строк сопоставлено"] += 1
                    reference[r["original"]] = their_text
                    if their_text.strip() != (r["translation"] or "").strip():
                        stat["расходимся"] += 1
                        if len(disagree) < 25:
                            disagree.append((pl.name, r["original"], r["translation"],
                                             their_text, r["status"]))

    for k, v in stat.most_common(12):
        print(f"  {k:<40}{v:>7}", file=out)
    print(f"\n  пар в справочнике: {len(reference):,}", file=out)
    for name, en, mine, theirs, st in disagree:
        print(f"\n  [{st}] {en[:56]!r}", file=out)
        print(f"      наш      {str(mine)[:60]!r}", file=out)
        print(f"      человек  {str(theirs)[:60]!r}", file=out)

    if args.dump and reference:
        Path(args.dump).write_text(
            json.dumps(reference, ensure_ascii=False, indent=0, sort_keys=True),
            encoding="utf-8")
        print(f"\n  справочник выгружен: {args.dump}", file=out)


if __name__ == "__main__":
    main()
