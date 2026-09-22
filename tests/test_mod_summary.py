"""О чём мод — по его собственным строкам.

Проход по всему корпусу раздавал моды с ПУСТЫМ контекстом: `mods = [(mod, strs, "")]`.
То есть модель переводила реплику из Legacy of the Dragonborn ровно так же, как из
мода на мечи — ни жанра, ни повторяющихся имён.

Имена здесь важнее жанра: именно на них мы и проигрываем (ACTI 34,6%, MGEF 66,1%
против WEAP 99,6%), и выдумать их модель не может.
"""
import sqlite3
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from translator.context import mod_summary as MS   # noqa: E402


class _Repo:
    def __init__(self, rows):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE strings (mod_name TEXT, rec_type TEXT, original TEXT)")
        self.db.executemany("INSERT INTO strings VALUES (?,?,?)", rows)


def setup_function(_fn):
    MS._CACHE.clear()


def test_the_summary_names_what_the_mod_is_made_of():
    repo = _Repo([("Mod", "INFO", "Some line of dialogue here, long enough to count."),
                  ("Mod", "INFO", "Another line of dialogue that is long enough."),
                  ("Mod", "BOOK", "A book about the history of the province.")])
    got = MS.build(repo, "Mod")
    assert "2 dialogue lines" in got and "1 books" in got


def test_a_name_that_recurs_is_offered_and_a_one_off_is_not():
    """Имя, встреченное однажды, ещё не имя мода — это может быть что угодно.

    Считается только то, что стоит НЕ в начале предложения: иначе «Perhaps» и
    «Please» попадают в имена наравне с «Metellus». Здесь это видно прямо — второе
    упоминание открывает фразу и потому не в счёт, и имени нужно третье.
    """
    repo = _Repo([("Mod", "INFO", "Talk to Metellus about the matter at hand."),
                  ("Mod", "INFO", "Metellus will not like what you have to say."),
                  ("Mod", "INFO", "Go and ask Metellus what he thinks of it."),
                  ("Mod", "INFO", "Someone called Wanderer passed through once.")])
    got = MS.build(repo, "Mod")
    assert "Metellus" in got
    assert "Wanderer" not in got


def test_technical_words_are_not_offered_as_names():
    """«Textures» и «Interface» приходят из путей к файлам, а не из сюжета.

    Предложить их как имена, которые надо переводить одинаково, — значит увести
    модель в сторону на каждой строке мода.
    """
    repo = _Repo([("Mod", "INFO", "Look in the Textures folder for the Interface files."),
                  ("Mod", "INFO", "The Textures and Interface folders are both there.")])
    got = MS.build(repo, "Mod")
    assert "Textures" not in got and "Interface" not in got


def test_a_word_opening_a_sentence_is_not_a_name():
    repo = _Repo([("Mod", "INFO", "Skyrim is cold. Winter never truly ends here."),
                  ("Mod", "INFO", "Winter is the season. Nothing grows in it at all.")])
    got = MS.build(repo, "Mod")
    assert "Winter" not in got


def test_a_mod_with_nothing_to_say_says_nothing():
    assert MS.build(_Repo([]), "Empty") == ""
    assert MS.build(None, "Mod") == ""


def test_the_summary_reaches_the_dispatch():
    """Справка обязана уходить в пакет: без этого она только украшает базу."""
    import inspect

    from translator.web.routes import jobs as _jobs
    src = inspect.getsource(_jobs._create_review_fleet_job)
    assert "mod_summary" in src
    assert '_ms.build(repo, mod)' in src
