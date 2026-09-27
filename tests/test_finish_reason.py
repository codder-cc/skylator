"""
Почему генерация кончилась — это знает бэкенд, а не текст.

Обрыв книги на полуслове определялся по тексту: источник длинный, ответ короче 70% и не
кончается знаком конца. 217 таких в корпусе, из них 167 из 179 просмотренных обрывались
посреди слова — то есть признак работал, но оставался догадкой.

Бэкенд всё это время знал точно. llama.cpp возвращает finish_reason «length», mlx_lm
сообщает то же или его видно по числу выданных сегментов против потолка.

Сигнал проведён насквозь: бэкенд запоминает причину, агент помечает обрезанную строку
и не отдаёт её как готовую.
"""
import ast
import io
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _src(rel: str) -> str:
    return io.open(ROOT / rel, encoding="utf-8").read()


def test_llamacpp_records_the_reason():
    s = _src("remote_worker/models/llamacpp_backend.py")
    assert "last_finish_reason" in s
    assert 'get("finish_reason")' in s, "причина берётся из ответа, а не выдумывается"


def test_mlx_records_the_reason():
    s = _src("remote_worker/models/mlx_backend.py")
    assert "last_finish_reason" in s
    assert 'len(segments) >= cap' in s, (
        "где mlx_lm причину не сообщает, упор в потолок виден по числу сегментов")


def _run_with(answers, originals):
    """Прогнать настоящий бегунок на временной базе: (строки результата, промпты).

    answers — ответы бэкенда по порядку; элемент (текст, причина) задаёт причину конца
    генерации ЭТОГО вызова, как её выставил бы настоящий бэкенд.
    """
    import asyncio, sys, tempfile
    from types import SimpleNamespace
    sys.path.insert(0, str(ROOT / "remote_worker"))
    from result_store import ResultStore
    from offline_translate import OfflineTranslateRunner

    class Backend:
        def __init__(self):
            self.answers = list(answers)
            self.last_finish_reason = None
            self.prompts = []

        def _infer(self, prompt, params=None, stop_check=None):
            self.prompts.append(prompt)
            a = self.answers.pop(0) if self.answers else ""
            a, self.last_finish_reason = a if isinstance(a, tuple) else (a, "stop")
            return a

    with tempfile.TemporaryDirectory() as d:
        store = ResultStore(Path(d) / "w.db")
        items = [{"string_id": n, "original": t, "mod_name": "M", "esp_name": "M.esp"}
                 for n, t in enumerate(originals, 1)]
        store.add_assignment("a", items=items)
        backend = Backend()

        async def go():
            await OfflineTranslateRunner(store, "a", {"params": {}}).run(
                SimpleNamespace(backend=backend), asyncio.get_running_loop())
        asyncio.run(go())
        rows = store.results_since(0)
        store.close()
    return rows, backend.prompts


def test_the_runner_refuses_the_cut_string_and_keeps_the_reason():
    """Обрезанная строка не уходит как готовая, и причина лежит рядом с ней."""
    rows, _ = _run_with([("1. Я знаю это место и помню старую", "length")],
                        ["I know this place and remember the old road."])
    assert rows[0]["status"] == "needs_review"
    assert rows[0]["finish_reason"] == "length"
    assert rows[0]["quality_score"] <= 60


def test_only_the_last_filled_string_is_marked():
    """Потолок рубит последнюю выданную строку. Те, что до неё, целы, и портить их
    статус значило бы отправить на переделку верную работу."""
    rows, _ = _run_with([("1. Я знаю это место и помню дорогу.\n2. Я помню мост и",
                          "length")],
                        ["I know this place and remember the road.",
                         "I remember the bridge and the river."])
    by_id = {r["string_id"]: r for r in rows}
    assert by_id[1]["status"] == "translated" and by_id[1]["finish_reason"] == "stop"
    assert by_id[2]["status"] == "needs_review" and by_id[2]["finish_reason"] == "length"


def test_each_single_retry_keeps_its_own_reason():
    """При повторе по одной причина — своя у каждого вызова. Раньше читалась одна, от
    последнего, и обрезанная первая строка уходила translated."""
    rows, prompts = _run_with(
        ["1. Я знаю это место и помню старую дорогу.",          # батч вернулся короче
         ("1. Я знаю это место и помню", "length"),              # строка 1 — обрезана
         ("1. Я помню мост.", "stop")],
        ["I know this place and remember the old road.", "I remember the bridge."])
    assert len(prompts) == 3
    by_id = {r["string_id"]: r for r in rows}
    assert by_id[1]["finish_reason"] == "length" and by_id[1]["status"] == "needs_review"
    assert by_id[2]["finish_reason"] == "stop" and by_id[2]["status"] == "translated"


def test_everything_still_parses():
    for rel in ("remote_worker/models/llamacpp_backend.py",
                "remote_worker/models/mlx_backend.py",
                "remote_worker/offline_translate.py"):
        ast.parse(_src(rel))
