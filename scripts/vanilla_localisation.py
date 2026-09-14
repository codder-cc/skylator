"""Официальная русская локализация Skyrim как таблица авторитета.

Игра поставляется со всеми языками: .strings/.ilstrings/.dlstrings сопоставляют id
строки → текст, и английская и русская таблицы одного плагина делят эти id. Выравнивание
по id даёт пары EN→RU без модели, без голосования и без сомнений — это тот текст,
который русский игрок видит в базовой игре.

Замер на этой установке (Skyrim - Interface.bsa, пять ванильных плагинов):

    официальных пар EN→RU                    25 984
    наших строк с точно совпавшим источником 70 774
        совпадает с официальным              22 587
        косметика (регистр, пунктуация)       2 148
        НАСТОЯЩЕЕ РАСХОЖДЕНИЕ                46 026
            из них имена (FULL)              25 704

Имена — та самая половина, ради которой затевалась корпусная консистентность, и здесь
голосовать не о чем:

    Dragonsreach        наш «Драконье Гнездо»   официально «Драконий Предел»
    The Bee and Barb    наш «Пчела и Барб»      официально «Пчела и жало»
    Snow-Shod Farm      наш «Ферма Сноу-Шод»    официально «Ферма Снегоходов»
    Candlehearth Hall   наш «Зал Свеча у очага» официально «Таверна Очаг и свеча»

Официальный перевод местами хуже нашего по букве — Snow-Shod это фамилия, а не снегоход.
Но модпак живёт внутри базовой игры: если ферма в ванильном квесте называется одним
именем, а в моде другим, это и есть тот самый дефект, который мы чиним. Узнаваемость
бьёт буквальную точность.

    python scripts/vanilla_localisation.py                 # отчёт
    python scripts/vanilla_localisation.py --dump out.json # выгрузить таблицу
"""
import collections
import io
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.strings_codec import parse_strings_bytes  # noqa: E402

out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

KINDS = {".strings": "strings", ".ilstrings": "ilstrings", ".dlstrings": "dlstrings"}
_COSMETIC_RE = re.compile(r"[\s\.:;,!?«»\"'()\[\]\-\u2013\u2014]+")
NAME_RECORDS = ("NPC_", "LCTN", "CELL", "WEAP", "ARMO", "ALCH", "MISC", "INGR",
                "BOOK", "QUST", "SPEL", "KEYM", "AMMO")


def cosmetic_key(text: str) -> str:
    """Текст без того, что отличает «Взять» от «Взять:» — регистра и пунктуации."""
    return _COSMETIC_RE.sub("", (text or "").lower())


def unpack_interface_bsa(bsa: Path, bsarch: Path, dest: Path) -> Path:
    """Распаковать архив с таблицами строк. Возвращает каталог strings/."""
    dest.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(bsarch), "unpack", str(bsa), str(dest), "-q", "-mt"],
                   check=True, capture_output=True)
    return dest / "strings"


def load_pairs(strings_dir: Path, language: str = "russian") -> dict[str, str]:
    """{английский текст: официальный перевод} по всем плагинам в каталоге.

    Источник, у которого официальных передач больше одной, выбрасывается целиком.
    Раньше здесь стоял setdefault — брал первую попавшуюся и молча терял остальные, а
    таблица авторитета, которая угадывает, авторитетом быть перестаёт:

        Frost Cloaked Spider  →  «Окутанный морозом паук»  ИЛИ  «Электрический паук-прыгун»

    Это два разных существа с одним английским именем в разных плагинах, и выбрать за
    игру тут нечем. Таких 709 из 25 984 — 2,7%, и верх этого списка вообще не переводы,
    а таблицы подстановки шрифтов: «j», «R», «x» против случайных кириллических глифов.
    """
    seen: dict[str, dict[str, int]] = {}
    for en_path in sorted(strings_dir.glob("*_english.*")):
        kind = KINDS.get(en_path.suffix.lower())
        if kind is None:
            continue
        ru_path = en_path.with_name(en_path.name.replace("_english.", f"_{language}."))
        if not ru_path.exists():
            continue
        try:
            en = parse_strings_bytes(en_path.read_bytes(), kind)
            ru = parse_strings_bytes(ru_path.read_bytes(), kind)
        except Exception as exc:                      # одна битая таблица не роняет весь ингест
            print(f"  пропущено {en_path.name}: {exc}", file=out)
            continue
        for sid, etext in en.items():
            rtext = ru.get(sid)
            if not etext or not rtext:
                continue
            e, r = etext.strip(), rtext.strip()
            if e and r and e != r:
                seen.setdefault(e, {})[r] = seen.setdefault(e, {}).get(r, 0) + 1
    pairs: dict[str, str] = {}
    dropped = 0
    for en, renderings in seen.items():
        distinct = {cosmetic_key(x) for x in renderings}
        if len(distinct) > 1:
            dropped += 1
            continue                      # игра сама называет это по-разному
        pairs[en] = max(renderings.items(), key=lambda kv: kv[1])[0]
    if dropped:
        print(f"  выброшено как неоднозначное: {dropped}", file=out)
    return pairs


def audit(repo_db, pairs: dict[str, str]) -> tuple[collections.Counter, list]:
    """Сверить корпус с таблицей авторитета. Ничего не меняет."""
    stat = collections.Counter()
    name_conflicts = []
    rows = repo_db.execute(
        "SELECT original, translation, status, rec_type, field_type FROM strings "
        "WHERE TRIM(COALESCE(original,'')) <> ''").fetchall()
    for r in rows:
        official = pairs.get((r["original"] or "").strip())
        if official is None:
            continue
        stat["source_matched"] += 1
        ours = (r["translation"] or "").strip()
        if not ours:
            stat["empty"] += 1
        elif ours == official:
            stat["agrees"] += 1
        elif cosmetic_key(ours) == cosmetic_key(official):
            stat["cosmetic"] += 1
        else:
            stat["disagrees"] += 1
            if r["rec_type"] in NAME_RECORDS and r["field_type"] == "FULL":
                stat["name_disagrees"] += 1
                name_conflicts.append((r["original"], ours, official))
    return stat, name_conflicts


def apply_names(repo, pairs: dict[str, str], dry: bool = True) -> int:
    """Привести имена к официальным. Только FULL у записей, где FULL — это имя.

    Базовая игра на этой установке уже русская (sLanguage=RUSSIAN), то есть карта и
    ванильные квесты показывают «Драконий Предел», пока мод в том же месте пишет
    «Драконье Гнездо». Это не вопрос вкуса, а расхождение, которое игрок видит на
    экране.

    Остальные 20 тысяч расхождений сюда не входят: среди них интерфейсные строки, где
    официальный текст несёт двоеточие под своё место в меню («Прочесть:»), и переносить
    это в мод вслепую нельзя.
    """
    import time

    from translator.validation.quality import compute_string_status

    now = time.time()
    changed = 0
    rows = repo.db.execute(
        "SELECT id, original, translation, rec_type, field_type FROM strings "
        "WHERE field_type='FULL' AND TRIM(COALESCE(translation,'')) <> ''").fetchall()
    for r in rows:
        if r["rec_type"] not in NAME_RECORDS:
            continue
        official = pairs.get((r["original"] or "").strip())
        ours = (r["translation"] or "").strip()
        if not official or not ours or ours == official:
            continue
        if cosmetic_key(ours) == cosmetic_key(official):
            continue
        changed += 1
        if dry:
            continue
        qs, _tok, _issues, status = compute_string_status(
            r["original"], official, None, r["rec_type"], r["field_type"])
        try:
            repo.insert_history(r["id"], r["translation"], "translated", None,
                                "vanilla:official", None, None)
        except Exception as exc:
            log_msg = f"vanilla: history for {r['id']}: {exc}"
            print(log_msg, file=out)
        repo.db.execute(
            "UPDATE strings SET translation=?, status=?, quality_score=?, "
            "source='vanilla', updated_at=? WHERE id=?",
            (official, status, qs, now, r["id"]))
    if not dry:
        repo.db.commit()
    return changed


def main() -> None:
    bsarch = next(Path("H:/Nolvus").rglob("BSArch.exe"), None)
    bsa = Path("H:/Nolvus/Instances/Nolvus Awakening/STOCK GAME/Data/Skyrim - Interface.bsa")
    if bsarch is None or not bsa.exists():
        print("не найден BSArch или Skyrim - Interface.bsa", file=out)
        return

    with tempfile.TemporaryDirectory() as tmp:
        strings_dir = unpack_interface_bsa(bsa, bsarch, Path(tmp))
        pairs = load_pairs(strings_dir)
    print(f"официальных пар EN→RU: {len(pairs):,}", file=out)

    if "--dump" in sys.argv:
        target = Path(sys.argv[sys.argv.index("--dump") + 1])
        target.write_text(json.dumps(pairs, ensure_ascii=False, indent=0), encoding="utf-8")
        print(f"выгружено в {target}", file=out)

    from translator.db.database import TranslationDB
    db = TranslationDB(ROOT / "cache" / "translations.db")
    stat, conflicts = audit(db, pairs)

    print(f"\nнаших строк с совпавшим источником: {stat['source_matched']:,}", file=out)
    for key, ru in (("agrees", "совпадает с официальным"),
                    ("cosmetic", "косметика (регистр, пунктуация)"),
                    ("disagrees", "настоящее расхождение"),
                    ("name_disagrees", "  из них имена (FULL)"),
                    ("empty", "у нас пусто")):
        print(f"  {ru:<34}{stat[key]:>8,}", file=out)

    if "--apply-names" in sys.argv:
        from translator.db.repo import StringRepo
        n = apply_names(StringRepo(db), pairs, dry=False)
        print(f"\nимён приведено к официальным: {n:,}", file=out)
        return

    print("\n=== имена, где официальный ответ закрывает спор ===", file=out)
    seen = set()
    for o, ours, official in conflicts:
        if o in seen or len(o) > 46:
            continue
        seen.add(o)
        print(f"  {o:<34} наш: {ours[:26]:<28} офиц: {official[:26]}", file=out)
        if len(seen) >= 20:
            break


if __name__ == "__main__":
    main()


# Ванильные FormID: Skyrim.esm 00, Update 01, Dawnguard 02, HearthFires 03, Dragonborn 04.
# Запись мода с таким FormID — это ПЕРЕОПРЕДЕЛЕНИЕ ванильной, тот же объект игры, уже
# названный официальной локализацией.
_VANILLA_PLUGINS = ("00", "01", "02", "03", "04")
_TAIL_COLON_RE = re.compile(r"[:：]\s*$")


def is_vanilla_override(form_id: str) -> bool:
    fid = (form_id or "").strip()
    return len(fid) >= 2 and fid[:2].upper() in _VANILLA_PLUGINS


def apply_vanilla_overrides(repo, pairs: dict[str, str], dry: bool = True) -> dict:
    """Выровнять по официальной локализации записи, переопределяющие ванильные.

    Имена применены отдельно. Здесь остальное — описания эффектов, цели квестов, реплики
    — но только там, где мод переопределяет ванильную запись: игрок видит тот же объект,
    и если базовая игра зовёт его «Магия», а мод «Магикка», это то же расхождение.

    Из 18 159 оставшихся расхождений таких 13 287. Собственные записи мода, у которых
    просто совпал английский текст, не трогаются: «Hello.» в чужом диалоге принадлежит
    автору мода, а не Bethesda.

    Не берётся и то, где официальный текст несёт двоеточие под своё место в меню
    («Прочесть:») или набран заглавными — это оформление интерфейса базовой игры, и в
    моде оно ни к чему.
    """
    import time

    from translator.validation.quality import compute_string_status

    now = time.time()
    stat = collections.Counter()
    rows = repo.db.execute(
        "SELECT id, original, translation, form_id, rec_type, field_type, source "
        "FROM strings WHERE TRIM(COALESCE(translation,'')) <> ''").fetchall()
    for r in rows:
        official = pairs.get((r["original"] or "").strip())
        ours = (r["translation"] or "").strip()
        if not official or not ours or ours == official or r["source"] == "vanilla":
            continue
        if cosmetic_key(ours) == cosmetic_key(official):
            continue
        if not is_vanilla_override(r["form_id"]):
            stat["своя запись мода — не трогаем"] += 1
            continue
        if _TAIL_COLON_RE.search(official) and not _TAIL_COLON_RE.search(ours):
            stat["подпись меню — не трогаем"] += 1
            continue
        if official.isupper() or (r["original"] or "").isupper():
            stat["заглавными — не трогаем"] += 1
            continue
        stat["выровнено"] += 1
        if dry:
            continue
        qs, _tok, _issues, status = compute_string_status(
            r["original"], official, None, r["rec_type"], r["field_type"])
        try:
            repo.insert_history(r["id"], r["translation"], "translated", None,
                                "vanilla:override", None, None)
        except Exception as exc:
            log_line = f"vanilla override: history for {r['id']}: {exc}"
            print(log_line, file=out)
        repo.db.execute(
            "UPDATE strings SET translation=?, status=?, quality_score=?, "
            "source='vanilla', updated_at=? WHERE id=?",
            (official, status, qs, now, r["id"]))
    if not dry:
        repo.db.commit()
    return dict(stat)
