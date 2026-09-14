"""Отрезать тестовый срез официальной локализации ДО того, как она войдёт в систему.

Порядок здесь необратим. Официальные пары идут в реестр имён, реестр — в глоссарий,
глоссарий — в промпт, а до 2 000 пар памяти переводов едут в каждом пакете. Если
отрезать срез после ингеста, любой замер модели покажет размер нашей памяти, а не
качество модели, и все кандидаты выйдут прекрасными.

Поэтому срез режется первым и по хешу источника, а не случайно: разбиение
воспроизводимо, переживает пересборку и не зависит от порядка чтения таблиц.

    data/vanilla_holdout.json  — 10%, НЕ попадает никуда, кроме замеров
    data/vanilla_names.json    — остальное, из которого строится реестр

Порядок необратим и в другую сторону: срез легко втянуть обратно, не заметив. Так и
вышло, когда реестр дополнялся официальной таблицей — 1 030 строк линейки въехали в
требования, то есть ответы на них поехали в промпт. Поэтому исключение живёт в трёх
местах, а не в одном:

    translator/validation/authority.py   таблица на воротах записи фильтрует срез
    scripts/widen_registry (scratchpad)  расширение реестра его пропускает
    этот файл                            сам срез

Что остаётся протечкой, и это признаётся, а не замазывается: 17 записей рукописного
глоссария совпадают со строками среза («Даэдра», «Лагерь», «Черный камень душ»).
Глоссарий писался руками задолго до среза, и вычищать из него базовую лексику ради
чистоты замера — портить продукт ради линейки. 17 из 2 552 это 0,7%.

Состояние линейки на 14 сентября: из 2 552 пар в корпусе есть 1 441, но 1 102 из них
уже переписаны таблицей в прошлых сессиях (source='vanilla') и как замер не годятся.
Чистых точек осталось 339, и замер по ним:

    точно 72,9%   по леммам 2,1%   половина лемм и больше 4,1%   расходится 20,9%
    средняя доля общих лемм 0,810

С этого дня правило авторитета срез не трогает, так что 339 точек остаются чистыми.
Выровнять их в самом конце работы: SKYLATOR_APPLY_HOLDOUT=1 снимает исключение.

    python scripts/vanilla_holdout.py            # отчёт
    python scripts/vanilla_holdout.py --write
"""
import hashlib
import io
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.vanilla_localisation import load_pairs, unpack_interface_bsa  # noqa: E402

out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

HOLDOUT_SHARE = 10          # каждая десятая пара по хешу
HOLDOUT_PATH = ROOT / "data" / "vanilla_holdout.json"
NAMES_PATH = ROOT / "data" / "vanilla_names.json"

# Имя, а не фраза и не подпись в интерфейсе.
#
# Первая версия брала всё короче пяти слов без конечной точки — и набрала ровно то, чего
# в реестре быть не должно. На 20 000 строк она дала 5 379 «нарушений», и верхушка
# целиком состоит из интерфейсных подписей базовой игры:
#
#     Damage → «Повреждение:»   550    ARMOR → «ДОСПЕХИ»   268
#     Right  → «Вправо»         223    Name  → «Название»  129
#
# Требовать «Повреждение» в описании заклинания, где правильно «урон», — это не проверка
# терминологии, а её порча. Причина в том, что одиночное общее слово неотличимо от имени
# по форме: «Damage» и «Dragonsreach» устроены одинаково.
#
# Поэтому имя здесь — минимум два слова. Это отсекает весь класс интерфейсных подписей и
# сохраняет то, ради чего реестр и нужен: составные имена предметов, мест и персонажей,
# которых в долге по подстрочным именам 7 670 из 7 670. Одиночные имена — «Dragonsreach»,
# «Whiterun» — приходят точным совпадением источника, где они и так однозначны.
_SENTENCE_RE = re.compile(r"[.!?…]\s|[.!?…]$|\n")
_WORD_RE = re.compile(r"[A-Za-z]")
_LABEL_RE = re.compile(r"[:：]\s*$")


def is_name(text: str, translation: str = "") -> bool:
    t = (text or "").strip()
    if not (3 <= len(t) <= 48) or not _WORD_RE.search(t):
        return False
    if _SENTENCE_RE.search(t):
        return False
    if not 2 <= len(t.split()) <= 5:
        return False
    # «Place → Поместить:» — двоеточие говорит, что это подпись, а не имя.
    return not _LABEL_RE.search(translation or "")


def corpus_names() -> set[str]:
    """Тексты, которые сама коллекция использует как имя записи.

    Подключение read-only и через uri: сборка реестра — чтение, и она не должна ждать
    блокировку записи. Через TranslationDB она её ждала: агенты в это время доставляли
    результаты, и сборка висела двадцать минут, ничего не делая.
    """
    import sqlite3

    from translator.validation.consistency import NAME_RECORDS
    path = (ROOT / "cache" / "translations.db").as_posix()
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        q = ("SELECT DISTINCT original FROM strings WHERE field_type='FULL' "
             "AND rec_type IN (%s)" % ",".join("?" * len(NAME_RECORDS)))
        return {(row[0] or "").strip() for row in con.execute(q, NAME_RECORDS)}
    finally:
        con.close()


def in_holdout(source: str) -> bool:
    """Детерминированно и воспроизводимо: десятая доля по хешу источника."""
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % HOLDOUT_SHARE == 0


def main() -> None:
    # Точный путь, а не обход диска: rglob по H:/Nolvus идёт через 1 945 папок модов
    # и стоит минуты на каждом запуске.
    bsarch = Path("H:/Nolvus/Instances/Nolvus Awakening/TOOLS/BSArch/BSArch.exe")
    if not bsarch.exists():
        bsarch = next(Path("H:/Nolvus").rglob("BSArch.exe"), None)
    bsa = Path("H:/Nolvus/Instances/Nolvus Awakening/STOCK GAME/Data/Skyrim - Interface.bsa")
    if bsarch is None or not bsa.exists():
        print("не найден BSArch или Skyrim - Interface.bsa", file=out)
        return
    with tempfile.TemporaryDirectory() as tmp:
        pairs = load_pairs(unpack_interface_bsa(bsa, bsarch, Path(tmp)))

    holdout = {en: ru for en, ru in pairs.items() if in_holdout(en)}
    train = {en: ru for en, ru in pairs.items() if not in_holdout(en)}
    names = {en: ru for en, ru in train.items() if is_name(en, ru)}

    # Третье сужение, и единственное, которое сработало по-настоящему. По форме имя от
    # интерфейсной фразы не отличается: «Mountain Flower» и «Pick up» устроены одинаково,
    # «TO STEAL» ловится внутри «to steal» в прозе. Спрашиваем корпус: имя — это текст,
    # который у нас самих где-то стоит в поле FULL записи-имени.
    #
    #     на 20 000 строк:  5 379 «нарушений» → 76 → 17
    #
    # И то, что осталось, целиком настоящее: The Warrens → «Муравейник», Tundra Cotton →
    # «Пушица», The Arcanaeum → «Арканеум», Bone Breaker → «Костолом».
    known = corpus_names()          # один раз, а не на каждой записи словаря
    names = {en: ru for en, ru in names.items() if en in known}

    print(f"официальных пар:        {len(pairs):>7,}", file=out)
    print(f"  holdout (10%):        {len(holdout):>7,}   в систему НЕ идёт", file=out)
    print(f"  остальное:            {len(train):>7,}", file=out)
    print(f"    из них имена:       {len(names):>7,}   → реестр", file=out)
    print(f"    фразы и реплики:    {len(train) - len(names):>7,}   → точное совпадение", file=out)

    hn = sum(1 for en, ru in holdout.items() if is_name(en, ru))
    print(f"\nв holdout имён: {hn:,}, фраз: {len(holdout) - hn:,}", file=out)

    if "--write" not in sys.argv:
        print("\n(--write запишет оба файла)", file=out)
        return
    HOLDOUT_PATH.write_text(json.dumps(holdout, ensure_ascii=False, indent=0),
                            encoding="utf-8")
    NAMES_PATH.write_text(json.dumps(names, ensure_ascii=False, indent=0),
                          encoding="utf-8")
    print(f"\nзаписано:\n  {HOLDOUT_PATH.relative_to(ROOT)}  {HOLDOUT_PATH.stat().st_size/1024:.0f} КБ"
          f"\n  {NAMES_PATH.relative_to(ROOT)}  {NAMES_PATH.stat().st_size/1024:.0f} КБ", file=out)


if __name__ == "__main__":
    main()
