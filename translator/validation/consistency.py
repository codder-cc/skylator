"""
Противоречия между строками, каждая из которых по отдельности безупречна.

`compute_string_status` отвечает на вопрос «допустима ли эта строка». Он видит одну
строку и обязан видеть только её: суждение должно зависеть от текста, а не от того, что
лежит в соседнем моде, иначе пересчёт перестанет быть повторяемым.

Но есть дефект, которого при таком взгляде не существует:

    Forsworn Briarheart  →  «Изгои Сердце-Корень» ×70
                            «Изгой-сердце-куст»   ×5

Обе строки: status=translated, score=100, issues=[], renders_as_garbage=false. Ни один
сигнал сам себе не противоречит. Ошибка живёт в отношении между строками, и увидеть её
можно только по корпусу.

Замер на живой коллекции — имена (field_type='FULL' у записей, у которых FULL это имя):

    5 718 имён переведены по-разному, 27 378 строк

Из чего это состоит — важнее общего числа, потому что определяет, что можно починить
без модели:

      495 кластеров (1 875 строк)  различаются ТОЛЬКО регистром и пунктуацией
      355                          различаются падежом или числом
       33                          плюс служебное слово («из», «в»)
    4 835                          конкурируют по смыслу
        из них 1 332 — транслитерация одной сущности: Мелвин/Мельвин, Джуджу/Джу-джу
        из них 1 863 — вариант встречается по одному разу, и частотность не говорит ничего

Отсюда единственное, что этот модуль решает сам: **регистр и пунктуация**. Буквы там
одни и те же, различается только оформление, и выбрать то, как коллекция пишет чаще —
безопасно.

Всё остальное он НЕ решает, и это принципиально. «Дом Климмек» ×7 против «Дом Климмка»
×2 — большинство держит несклоняемую форму, то есть неправильную. «Солдат штормовиков»
×147 против «Солдат Столбов» ×62 — большинство право. Частотность не доказывает
правильность, она только показывает, что коллекция не определилась.

Поэтому модуль ничего не объявляет хорошим. Он производит работу: одно решение на
кластер вместо строки на каждое вхождение — 5 718 решений вместо 27 378 — а записывает
результат существующий путь, через save_string и ворота слияния.
"""
from __future__ import annotations

import collections
import logging
import re

log = logging.getLogger(__name__)

# Типы записей, у которых FULL — это имя, которое игрок читает. FACT сюда не входит:
# имя фракции живёт в редакторе, а не на экране.
NAME_RECORDS = ("NPC_", "LCTN", "CELL", "WEAP", "ARMO", "ALCH", "MISC", "INGR",
                "BOOK", "QUST", "SPEL", "KEYM", "AMMO")

_CYRILLIC_RE = re.compile(r"[А-яЁё]")
_TRIM_RE = re.compile(r"[\s\-–—_,.:;!?«»\"'()\[\]]+")

MIN_LEN, MAX_LEN = 4, 60


class Cluster:
    """Одно английское имя и все русские варианты, которыми коллекция его называет."""

    __slots__ = ("original", "variants", "rec_types")

    def __init__(self, original: str):
        self.original = original
        self.variants: collections.Counter = collections.Counter()
        self.rec_types: set[str] = set()

    @property
    def total(self) -> int:
        return sum(self.variants.values())

    @property
    def ranked(self) -> list[tuple[str, int]]:
        # По убыванию частоты, при равенстве — по тексту, чтобы порядок не зависел от
        # того, в каком порядке строки пришли из базы.
        return sorted(self.variants.items(), key=lambda kv: (-kv[1], kv[0]))

    def __repr__(self) -> str:
        return f"<Cluster {self.original!r} {len(self.variants)} variants>"


def _casefold_key(text: str) -> str:
    """Текст без регистра, пробелов и пунктуации — то, что остаётся от оформления."""
    return _TRIM_RE.sub("", (text or "").lower())


def casing_only(cluster: Cluster) -> str | None:
    """Канонический вариант, если варианты различаются ТОЛЬКО оформлением.

    «Теневой Атронах» и «Теневой атронах» — одни и те же буквы в одном и том же порядке;
    разное только то, как коллекция пишет имена. Выбирается то, что чаще, и это
    безопасно именно потому, что смысл у вариантов один.

    Возвращает None везде, где буквы различаются: там решение за моделью или человеком,
    и частотность его не заменяет.
    """
    if len(cluster.variants) < 2:
        return None
    if len({_casefold_key(v) for v in cluster.variants}) != 1:
        return None
    return cluster.ranked[0][0]


def find_name_clusters(repo, min_len: int = MIN_LEN, max_len: int = MAX_LEN) -> list[Cluster]:
    """Имена, которые коллекция переводит больше чем одним способом.

    Только принятые строки: вопрос в том, что уже считается готовым. Только записи, у
    которых FULL действительно имя, и только переводы с кириллицей — строка, оставленная
    английской, это работа другого правила, а не расхождение.
    """
    by_source: dict[str, Cluster] = {}
    sql = ("SELECT original, translation, rec_type FROM strings "
           "WHERE status='translated' AND field_type='FULL' "
           "AND TRIM(COALESCE(translation,'')) <> '' AND translation <> original")
    for row in repo.db.execute(sql).fetchall():
        rec = row["rec_type"]
        if rec not in NAME_RECORDS:
            continue
        original = (row["original"] or "").strip()
        translation = (row["translation"] or "").strip()
        if not (min_len <= len(original) <= max_len):
            continue
        if not _CYRILLIC_RE.search(translation):
            continue
        c = by_source.get(original)
        if c is None:
            c = by_source[original] = Cluster(original)
        c.variants[translation] += 1
        c.rec_types.add(rec)
    return [c for c in by_source.values() if len(c.variants) > 1]


def split_clusters(clusters: list[Cluster]) -> tuple[list[tuple[Cluster, str]], list[Cluster]]:
    """(что чинится оформлением, что требует решения)."""
    mechanical: list[tuple[Cluster, str]] = []
    decide: list[Cluster] = []
    for c in clusters:
        canon = casing_only(c)
        if canon is not None:
            mechanical.append((c, canon))
        else:
            decide.append(c)
    return mechanical, decide


def apply_casing(repo, mechanical: list[tuple[Cluster, str]], job=None) -> int:
    """Привести оформление к тому, как коллекция пишет чаще. Меняет только те строки,
    которые сейчас пишутся иначе; текст остаётся тем же с точностью до регистра."""
    import time

    changed = 0
    now = time.time()
    for c, canon in mechanical:
        for variant in c.variants:
            if variant == canon:
                continue
            rows = repo.db.execute(
                "SELECT id, translation FROM strings WHERE original=? AND field_type='FULL' "
                "AND TRIM(translation)=TRIM(?) AND status='translated'",
                (c.original, variant)).fetchall()
            for r in rows:
                try:
                    repo.insert_history(r["id"], r["translation"], "translated", None,
                                        "consistency:casing", None, None)
                except Exception as exc:
                    log.debug("consistency: history for %s: %s", r["id"], exc)
                repo.db.execute(
                    "UPDATE strings SET translation=?, updated_at=? WHERE id=?",
                    (canon, now, r["id"]))
                changed += 1
        if job is not None and changed and changed % 200 == 0:
            job.add_log(f"consistency: {changed} string(s) brought to one spelling")
    repo.db.commit()
    return changed


def report(clusters: list[Cluster]) -> dict:
    """Сводка для лога джоба — без неё массовая операция остаётся непрозрачной."""
    mechanical, decide = split_clusters(clusters)
    return {
        "clusters": len(clusters),
        "rows": sum(c.total for c in clusters),
        "casing_only": len(mechanical),
        "casing_rows": sum(c.total for c, _ in mechanical),
        "need_decision": len(decide),
        "decision_rows": sum(c.total for c in decide),
        "no_frequency_signal": sum(1 for c in decide if max(c.variants.values()) == 1),
    }


# ── две кнопки одной записи с одним переводом ────────────────────────────────
#
#     Yes  →  «Нет»
#     No   →  «Нет»
#
# Игрок жмёт «Нет» и получает согласие. Обе строки по отдельности безупречны: «Нет» —
# нормальное русское слово, токены целы, длина верна, счёт 100. Неверна только связь
# между ними, поэтому compute_string_status этого увидеть не может — он видит одну
# строку.
#
# Замер на живом корпусе: 287 записей, где разные источники получили один и тот же
# перевод. 250 из них MESG/ITXT — это списки кнопок, то есть худшее место для такой
# ошибки.
#
# Отдельно стоит запомнить, КАК одна ошибка стала 254 строками: модель ошиблась один
# раз 6 сентября, и разнос по двойникам скопировал ответ в 253 других мода. Дедуп —
# усилитель и для верной работы, и для неверной, и во втором случае он превращает
# единичный промах в класс.

_BUTTON_FIELDS = ("ITXT",)


def find_collapsed_records(repo, only_buttons: bool = False) -> list[dict]:
    """Записи, где два разных источника получили один перевод.

    Группировка по (мод, файл, form_id, тип поля) — это одна запись игры, и внутри неё
    список кнопок или стадий обязан различаться, раз различаются источники.
    """
    where = ["status='translated'", "TRIM(COALESCE(translation,'')) <> ''",
             "translation <> original"]
    if only_buttons:
        where.append("field_type IN (%s)" % ",".join("'%s'" % f for f in _BUTTON_FIELDS))
    sql = f"""SELECT mod_name, esp_name, form_id, field_type, rec_type,
                     COUNT(DISTINCT original) AS n_src,
                     COUNT(DISTINCT translation) AS n_dst
              FROM strings WHERE {' AND '.join(where)}
              GROUP BY mod_name, esp_name, form_id, field_type
              HAVING n_src > 1 AND n_dst < n_src"""
    return [dict(r) for r in repo.db.execute(sql).fetchall()]


# «Yes» и «No» решать не нужно: это не вопрос вкуса и не вопрос контекста. Список
# держится маленьким намеренно — сюда попадает только то, у чего один правильный ответ
# в любом окружении.
UNAMBIGUOUS = {
    "yes": "Да",
    "no": "Нет",
    "cancel": "Отмена",
}
# «OK» сюда не входит намеренно: 45 строк пишут «ОК» кириллицей, 37 — «OK» латиницей,
# и обе формы правильны. Это вопрос единообразия, а не верности, и авторитета выбрать
# за коллекцию у этого модуля нет.


def fix_unambiguous_buttons(repo, dry: bool = True) -> dict:
    """Привести кнопки с единственным правильным ответом в порядок.

    Только там, где перевод сейчас НЕ тот: «No → Нет» уже верно и не трогается.
    """
    import time

    from translator.validation.quality import compute_string_status

    now = time.time()
    changed: dict[str, int] = {}
    for src, want in UNAMBIGUOUS.items():
        rows = repo.db.execute(
            "SELECT id, original, translation, rec_type, field_type FROM strings "
            "WHERE LOWER(TRIM(original))=? AND TRIM(COALESCE(translation,'')) <> '' "
            "AND TRIM(translation) <> ?", (src, want)).fetchall()
        for r in rows:
            changed[src] = changed.get(src, 0) + 1
            if dry:
                continue
            qs, _tok, _issues, status = compute_string_status(
                r["original"], want, None, r["rec_type"], r["field_type"])
            try:
                repo.insert_history(r["id"], r["translation"], "translated", None,
                                    "consistency:button", None, None)
            except Exception as exc:
                log.debug("button fix: history for %s: %s", r["id"], exc)
            repo.db.execute(
                "UPDATE strings SET translation=?, status=?, quality_score=?, "
                "updated_at=? WHERE id=?", (want, status, qs, now, r["id"]))
    if not dry:
        repo.db.commit()
    return changed
