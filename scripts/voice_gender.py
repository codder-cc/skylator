"""Пол говорящего — из озвучки, а не из догадки.

В игре видели женского персонажа, говорящего о себе в мужском роде. Проверить это нечем:
в базе есть текст реплики и её FormID, но нет ни малейшего указания, кто её произносит.
Модель выбирает род наугад, и замер это подтверждает — в диалогах 9 726 мужских форм
первого лица против 3 318 женских, без всякой связи с говорящим.

Но признак есть, и он механический. Озвучка Skyrim разложена так:

    Sound/Voice/<плагин>/<ТипГолоса>/<Квест>_<Тема>_<FORMID>_<n>.fuz

Тип голоса называет пол прямо — FemaleArgonian, MaleNord, FemaleEvenToned, — а в имени
файла стоит FormID записи INFO, то есть ровно тот ключ, по которому строка лежит у нас.
Ничего угадывать не требуется.

Оговорки, которые важнее самой идеи:

    нейтральных типов почти нет, но они бывают (детские, существа), и такие в ответ не
    попадают вовсе — лучше промолчать, чем приписать пол;

    ключ — не весь FormID, а его последние шесть знаков. Верхний байт это индекс
    плагина в порядке загрузки, и он у каждой установки свой: в базе реплика лежит как
    00020121, а файл озвучки называется ..._0020166B_1.fuz. На проверенном моде по
    полному идентификатору не совпало НИ ОДНОЙ записи из тридцати четырёх, по последним
    шести — все тридцать четыре;

    у одной реплики бывает несколько файлов с разными типами голоса: строку произносят
    разные персонажи. Если среди них есть и мужские, и женские, пол не определён, и это
    тоже повод промолчать, а не выбрать большинство;

    озвучка бывает упакована в BSA, и там её больше, чем свободной. BSArch умеет
    показывать содержимое архива без распаковки (`-list`), поэтому читаются и они —
    497 архивов, результат кладётся в кэш, чтобы не перечитывать каждый раз;

    тип голоса бывает свой, модовый, и тогда пол по имени не читается:
    «aclvoicemelvin» это мужчина, но сказать это может только человек. Такие в ответ не
    попадают.

    python scripts/voice_gender.py                 # отчёт о покрытии
    python scripts/voice_gender.py --dump out.json # выгрузить карту FormID → пол
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

MODS_DIR = Path("H:/Nolvus/Instances/Nolvus Awakening/MODS/mods")
# FormID в имени файла: восемь шестнадцатеричных знаков между подчёркиваниями.
_FORMID_RE = re.compile(r"_([0-9A-Fa-f]{8})_\d+\.(?:fuz|wav|lip)$")
_FEMALE_RE = re.compile(r"female", re.I)
_MALE_RE = re.compile(r"male", re.I)


def gender_of(voice_type: str) -> str | None:
    """Пол по имени типа голоса. None — когда тип ничего не говорит.

    Порядок проверок важен: «female» содержит в себе «male», и поиск мужского первым
    записал бы всех женщин в мужчины.
    """
    if _FEMALE_RE.search(voice_type):
        return "f"
    if _MALE_RE.search(voice_type):
        return "m"
    return None


# Внутри BSA путь записан обратными слэшами, и класс символов приходится собирать из
# chr(92): написанный литералом он превращается в экранирование прямого слэша, обратный
# слэш из класса исчезает, и совпадений нет ни одного — 497 архивов читаются впустую.
_SEP = "[" + chr(92) + chr(92) + "/]+"
_VOICE_PATH_RE = re.compile(
    "sound" + _SEP + "voice" + _SEP + "([^" + chr(92) + "/]+)" + _SEP
    + "([^" + chr(92) + "/]+)" + _SEP + "(.+)$", re.I)
BSARCH = Path("H:/Nolvus/Instances/Nolvus Awakening/TOOLS/BSArch/BSArch.exe")


def _bsa_voice_paths(bsa: Path) -> list[tuple[str, str, str]]:
    """(плагин, тип голоса, имя файла) из содержимого архива, без распаковки."""
    import subprocess
    try:
        r = subprocess.run([str(BSARCH), str(bsa), "-list"],
                           capture_output=True, timeout=300)
    except Exception:
        return []
    got = []
    for line in r.stdout.decode("utf-8", "replace").splitlines():
        m = _VOICE_PATH_RE.search(line.strip())
        if m:
            got.append((m.group(1), m.group(2), m.group(3)))
    return got


def scan_bsa(mods_dir: Path, cache: Path) -> dict:
    """{(плагин, FormID6): {пол}} из BSA. Список архивов кэшируется на диск."""
    import json as _json
    known = {}
    if cache.exists():
        try:
            known = _json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            known = {}
    seen: dict = collections.defaultdict(set)
    changed = False
    bsas = sorted(mods_dir.glob("*/*.bsa"))
    for i, bsa in enumerate(bsas, 1):
        key = f"{bsa.parent.name}/{bsa.name}"
        if key not in known:
            known[key] = [list(x) for x in _bsa_voice_paths(bsa)]
            changed = True
            if i % 50 == 0:
                print(f"      прочитано архивов: {i}/{len(bsas)}", file=out, flush=True)
        for plugin, vt, fname in known[key]:
            g = gender_of(vt)
            if g is None:
                continue
            m = _FORMID_RE.search(fname)
            if m:
                seen[(plugin.lower(), m.group(1).upper()[2:])].add(g)
    if changed:
        cache.write_text(_json.dumps(known), encoding="utf-8")
    return seen


def scan(mods_dir: Path = MODS_DIR, with_bsa: bool = True) -> tuple[dict, collections.Counter]:
    """{(плагин, FormID): 'm'|'f'} по озвучке — свободной и упакованной."""
    seen: dict[tuple[str, str], set] = collections.defaultdict(set)
    stat: collections.Counter = collections.Counter()
    if with_bsa:
        cache = Path(__file__).resolve().parents[1] / "cache" / "bsa_voice_index.json"
        cache.parent.mkdir(exist_ok=True)
        for k, gs in scan_bsa(mods_dir, cache).items():
            seen[k] |= gs
        stat["реплик из BSA"] = len(seen)
    for voice_dir in mods_dir.glob("*/Sound/Voice"):
        for plugin_dir in voice_dir.iterdir():
            if not plugin_dir.is_dir():
                continue
            plugin = plugin_dir.name.lower()
            for vt_dir in plugin_dir.iterdir():
                if not vt_dir.is_dir():
                    continue
                g = gender_of(vt_dir.name)
                if g is None:
                    stat["тип голоса без пола"] += 1
                    continue
                for f in vt_dir.iterdir():
                    m = _FORMID_RE.search(f.name)
                    if not m:
                        continue
                    stat["файлов озвучки"] += 1
                    seen[(plugin, m.group(1).upper()[2:])].add(g)
    gender: dict[tuple[str, str], str] = {}
    for key, gs in seen.items():
        if len(gs) == 1:
            gender[key] = next(iter(gs))
        else:
            stat["реплика звучит и мужским, и женским — пол не определён"] += 1
    stat["реплик с определённым полом"] = len(gender)
    return gender, stat


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump")
    ap.add_argument("--mods", default=str(MODS_DIR))
    ap.add_argument("--no-bsa", action="store_true")
    args = ap.parse_args()

    gender, stat = scan(Path(args.mods), with_bsa=not args.no_bsa)
    for k, v in stat.most_common():
        print(f"  {k:<50}{v:>8}", file=out)

    con = sqlite3.connect(f"file:{ROOT / 'cache' / 'translations.db'}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA cache_size=-400000")
    rows = con.execute(
        "SELECT esp_name, form_id, rec_type FROM strings "
        "WHERE rec_type='INFO' AND TRIM(COALESCE(translation,'')) <> ''").fetchall()
    hit = sum(1 for r in rows
              if ((r["esp_name"] or "").lower(), (r["form_id"] or "").upper()[-6:]) in gender)
    print(f"\n  строк INFO в базе: {len(rows):,}", file=out)
    print(f"  из них пол известен: {hit:,}  ({hit / max(len(rows), 1):.1%})", file=out)

    if args.dump:
        Path(args.dump).write_text(
            json.dumps({f"{p}|{f}": g for (p, f), g in gender.items()},
                       ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")
        print(f"\n  карта выгружена: {args.dump}  ({len(gender):,} реплик)", file=out)


if __name__ == "__main__":
    main()
