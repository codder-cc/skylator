"""Смотритель парка: не давать машинам простаивать и расшивать их обычные тупики.

Пакет кончается в непредсказуемый момент, и простой до сих пор ловился глазами: за один
день машина трижды стояла с нулём заданий, пока я смотрел в другую сторону. Очередь
ревью при этом не пуста, то есть это чистая потеря.

Смотритель опрашивает мастера раз в минуту и выдаёт работу тем, у кого пакетов нет.
Сперва `terms` — исправление одного термина на строке, по замерам 88% против 50% у
слепого перевода. Когда терминологических нарушений не остаётся, идёт `flagged` —
слепой перевод того, что правила отвергли.

Кроме простоя он расшивает два тупика, каждый из которых стоил времени вживую:

    пакет, о котором знает только мастер — после перезапуска агента назначение к нему
    не возвращается, мастер считает пакет выданным и не предлагает заново, а агент
    просит работу. Обе стороны довольны, машина стоит;

    воскрешённый остаток — мастер хранит пакеты на диске и после своего перезапуска
    возвращает их все, включая снятые. Агент по ним не работает, а строки числятся
    выданными и не попадают ни в одну новую раздачу.

Оба признака требуют выдержки, и её величина выведена из наблюдения, а не выбрана:
два опроса для первого (между пакетами такое состояние бывает на пару секунд) и
пятнадцать для второго (книга под потолком 16 384 токена считается около пяти минут,
и пакет с одной книгой в работе не должен выглядеть воскрешённым).

Порция на машину ограничена по той же причине: раздача отдаёт всю очередь тому, кто
освободился первым, и 4 113 строк однажды ушли на самую медленную машину парка, где
лежали бы сутки, пока быстрые стоят. Раздано работу назад не забрать.

    python scripts/fleet_keeper.py                      # 8 часов, порция 700
    python scripts/fleet_keeper.py --skip darwin-int00mac-7PKF2W   # машина занята хозяином
    python scripts/fleet_keeper.py --hours 2 --chunk 300
"""
import argparse
import io
import json
import sys
import time
import urllib.request

out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
BASE = "http://127.0.0.1:5000"


def get(url, t=60):
    return json.load(urllib.request.urlopen(BASE + url, timeout=t))


def post(url, payload=None, t=1800):
    req = urllib.request.Request(
        BASE + url, data=json.dumps(payload or {}).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST")
    return json.load(urllib.request.urlopen(req, timeout=t))


def stamp() -> str:
    return time.strftime("%H:%M:%S")


# Полоса длины на машину, и она выведена из двух замеров, а не из удобства.
#
# Очередь ревью на 85% состоит из коротких строк, но по ОБЪЁМУ на 94% из книг: 950 строк
# длиннее 1 200 знаков несут 6,3 млн знаков из 6,7. Раздавать её вслепую значит посадить
# все машины на книги, пока восемь тысяч коротких строк ждут, — и ровно это произошло:
# очередь падала на 2–3 строки в минуту при трёх работающих машинах.
#
# Куда что идти:
#   llama.cpp на 5080 — самая быстрая машина парка (62 токена/с против 41 у MLX), но её
#   контекст 8 192 токена на ВСЁ, промпт плюс ответ. Источник на 6 000 знаков это ~2 400
#   токенов промпта плюс столько же ответа, и это последнее, что туда влезает. Поднять
#   контекст нельзя: из 16,3 ГБ VRAM 11,9 уже занято, при том что сама модель 3,8 ГБ, —
#   остальное KV-кэш, и удвоение его не поместится. Ей всё до 6 000 знаков.
#
#   MLX предела по контексту не имеет (151 000 и 580 000 по опросу машин), поэтому книги
#   длиннее идут только туда, и потолок вывода поднимается под длину: при стандартных
#   2 048 токенах книга возвращается обрезанной на полуслове — так обрезано 154 книги,
#   половина из тех, что длиннее 10 000 знаков.
#
#   Медленная машина парка (5,7 токена/с) получает только короткое: там на строку уходят
#   десятки токенов, а не тысячи, и её вклад виден. Средняя книга на ней считается минут
#   двадцать — 254 таких книги это две недели.
_BANDS = {
    "windows-DeadLine":       (None, 6000, 3072),
    "darwin-int00mac-5YVL25": (None,  400, 2048),
    "darwin-int00mac-7PKF2W": (6000, None, 16384),
}
_DEFAULT_BAND = (None, 1200, 2048)


def band(label: str):
    """(min_chars, max_len, max_tokens) для машины; незнакомой — короткое и безопасное."""
    return _BANDS.get(label, _DEFAULT_BAND)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=8.0)
    ap.add_argument("--every", type=int, default=60)
    # Порция на машину. Без ограничения раздача отдаёт всю очередь тому, кто освободился
    # первым, — и 4 113 строк ушли на M1, самую медленную из трёх (3 стр/с против 60),
    # где они лежали бы сутки, пока быстрые машины стоят. Раздано работу назад не
    # забрать, поэтому порция должна кончаться быстрее, чем освобождается сосед.
    ap.add_argument("--chunk", type=int, default=700)
    # Машина может понадобиться хозяину. Раздавать на неё нельзя, и снимать с неё чужие
    # пакеты тоже: она не «зависла», она занята не нами.
    ap.add_argument("--skip", action="append", default=[],
                    help="метка машины, которую не трогать (можно несколько раз)")
    args = ap.parse_args()
    skip = set(args.skip)
    deadline = time.time() + args.hours * 3600
    scopes = ["terms", "flagged"]
    scope_i = 0
    stuck: dict = {}        # метка → сколько опросов подряд машина стоит с пакетом
    zero: dict = {}         # id пакета → сколько опросов подряд он на нуле

    while time.time() < deadline:
        try:
            workers = get("/api/workers")
            stats = get("/api/stats")
        except Exception as exc:
            print(f"{stamp()}  мастер не отвечает: {exc}", file=out, flush=True)
            time.sleep(args.every)
            continue

        queue = int(stats.get("needs_review") or 0) + int(stats.get("pending_strings") or 0)

        # Воскрешённый остаток. Мастер хранит пакеты на диске и после перезапуска
        # возвращает их все, включая снятые: у M5 так оказалось три пакета, один рабочий
        # и два по 1 759 и 9 997 строк с нулём сделанного. Агент по ним не работает, а
        # мастер считает строки выданными, и они не попадают ни в одну новую раздачу —
        # 11 756 строк выпали из очереди молча.
        #
        # Признак — ноль сделанного рядом с пакетом, где работа идёт. Но нужна выдержка:
        # свежепоставленный пакет тоже стоит на нуле, и без выдержки смотритель снёс бы
        # собственную раздачу через минуту после неё.
        #
        # Выдержка считается по самой долгой единице работы, а не наугад: книга под
        # потолком 16 384 токена на 57 токенах в секунду считается около пяти минут, и
        # порога в пять опросов не хватило бы — пакет с одной книгой в работе выглядел бы
        # воскрешённым. Пятнадцать минут длиннее любой книги, а воскрешённый остаток
        # лежит нулём сколько угодно, так что различает надёжно.
        for w in workers:
            if w["label"] in skip:
                continue
            jobs = w.get("offline_jobs") or []
            if len(jobs) < 2 or not any(x.get("done") for x in jobs):
                for x in jobs:
                    zero.pop(x.get("offline_job_id"), None)
                continue
            for x in jobs:
                aid = x.get("offline_job_id")
                if x.get("done") or not aid:
                    zero.pop(aid, None)
                    continue
                zero[aid] = zero.get(aid, 0) + 1
                if zero[aid] < 15:
                    continue
                try:
                    post(f"/api/workers/{w['label']}/cancel-offline",
                         {"offline_job_id": aid}, t=300)
                    print(f"{stamp()}  {w['label']}: снят воскрешённый {aid[:8]} "
                          f"({x.get('total')} строк, {zero[aid]} опросов на нуле)",
                          file=out, flush=True)
                except Exception as exc:
                    print(f"{stamp()}  {w['label']}: снять {aid[:8]} не удалось: {exc}",
                          file=out, flush=True)
                zero.pop(aid, None)

        # Пакет, о котором знает только мастер. После перезапуска агента назначение к
        # нему не возвращается: мастер считает пакет выданным и не предлагает его
        # заново, а агент о нём не знает и просит работу. Обе стороны довольны, машина
        # стоит. Так DeadLine простоял четверть часа на 0/378, и расшивается это только
        # снятием пакета: он возвращается в пул и раздаётся заново.
        #
        # Признак — пакет есть, открытых назначений нет, агент сообщает о голоде. Один
        # опрос не доказательство: между пакетами такое состояние бывает на пару секунд,
        # поэтому нужно два подряд.
        for w in workers:
            if w["label"] in skip:
                continue
            jobs = w.get("offline_jobs") or []
            health = w.get("health") or {}
            starved = (jobs and not health.get("open_assignments")
                       and health.get("idle_starved")
                       and not any(x.get("done") for x in jobs))
            label = w["label"]
            if not starved:
                stuck.pop(label, None)
                continue
            stuck[label] = stuck.get(label, 0) + 1
            if stuck[label] < 2:
                continue
            for j in jobs:
                aid = j.get("offline_job_id")
                if not aid:
                    continue
                try:
                    res = post(f"/api/workers/{label}/cancel-offline",
                               {"offline_job_id": aid}, t=300)
                    print(f"{stamp()}  {label}: пакет {aid[:8]} висел без работы — снят, "
                          f"в пул вернулось {res.get('returned_to_pool')}",
                          file=out, flush=True)
                except Exception as exc:
                    print(f"{stamp()}  {label}: снять {aid[:8]} не удалось: {exc}",
                          file=out, flush=True)
            stuck.pop(label, None)
            workers = get("/api/workers")
            break

        idle = [w["label"] for w in workers
                if w.get("alive") and w["label"] not in skip
                and not (w.get("offline_jobs") or [])]
        busy = [(w["label"], sum(x.get("done", 0) for x in (w.get("offline_jobs") or [])),
                 sum(x.get("total", 0) for x in (w.get("offline_jobs") or [])))
                for w in workers if (w.get("offline_jobs") or [])]

        if queue == 0:
            print(f"{stamp()}  очередь пуста — смотритель закончил", file=out, flush=True)
            return
        if not idle:
            line = "  ".join(f"{lbl.split('-')[-1]} {d}/{t}" for lbl, d, t in busy)
            print(f"{stamp()}  все заняты: {line}   очередь {queue}", file=out, flush=True)
            time.sleep(args.every)
            continue

        scope = scopes[scope_i % len(scopes)]
        for label in idle:
            lo, hi, ceiling = band(label)
            opts = {"scope": scope, "machines": [label], "max_tokens": ceiling,
                    "limit": args.chunk}
            if lo:
                opts["min_chars"] = lo
            if hi:
                opts["max_len"] = hi
            try:
                job = post("/jobs/create", {"type": "review_strings", "options": opts})
                jid = job.get("job_id")
                print(f"{stamp()}  {scope} {lo or 0}–{hi or '∞'} знаков → {label}  "
                      f"потолок {ceiling}  (очередь {queue})", file=out, flush=True)
                # Ждём, пока раздача дойдёт до пакета, иначе следующий круг увидит ту же
                # машину свободной и выдаст ей вторую порцию поверх первой.
                for _ in range(24):
                    time.sleep(5)
                    d = get(f"/api/jobs/{jid}")
                    if d.get("status") in ("done", "failed", "cancelled",
                                           "offline_dispatched"):
                        for line in (d.get("log_lines") or [])[-2:]:
                            print(f"      {str(line)[:120]}", file=out, flush=True)
                        break
            except Exception as exc:
                print(f"{stamp()}  раздача {scope} на {label} не удалась: {exc}",
                      file=out, flush=True)
        # Если область ничего не дала — пробуем следующую на следующем круге.
        scope_i += 1
        time.sleep(args.every)

    print(f"{stamp()}  срок смотрителя истёк", file=out, flush=True)


if __name__ == "__main__":
    main()
