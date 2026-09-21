"""Пройти по модам, у которых есть чужой перевод, и взять из него бесспорное.

Донор стоит применять ДО машин, а не после: строка, которую уже перевёл человек, не
должна стоить машинного времени. Сейчас порядок обратный — машина вслепую пересказывает
то, что лежит переведённым на Nexus.

Замер, определивший политику: на классе, где ответ известен заранее, донор побил нас 258
раз и проиграл 90. Поэтому берём не всё, а два случая:

    у нас пусто                     — чистый выигрыш
    расхождение, и имя подтверждает
    официальная локализация         — две независимые инстанции против одной машины

Остальные расхождения не трогаем: это мнение против мнения, и решать его должен человек,
глядя на строку.

Донор есть у 52% модов, а по строкам это 78% корпуса — то есть крупные моды, где и
сидит большая часть дефектов, почти все покрыты.

    python scripts/donor_pass.py --top 20            # сухой прогон
    python scripts/donor_pass.py --top 20 --write
"""
from __future__ import annotations

import argparse
import io
import json
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
BASE = "http://127.0.0.1:5000"


def _get(path: str, timeout: int = 600):
    return json.load(urllib.request.urlopen(BASE + path, timeout=timeout))


def _post(path: str, payload: dict, timeout: int = 3600):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST")
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def sweep_parts(donors_dir: Path, older_than_sec: float = 3600) -> int:
    """Убрать недокачанные хвосты, которые уже никто не докачает.

    `.part` живёт ради возобновления, и это правильно — пока скачивание идёт. Оборванное
    скачивание оставляет его навсегда, а хвост от донора на 400 мегабайт занимает ровно
    столько же, сколько сам донор.
    """
    freed = 0
    now = time.time()
    for p in donors_dir.glob("*.part"):
        try:
            if now - p.stat().st_mtime < older_than_sec:
                continue        # возможно, качается прямо сейчас
            freed += p.stat().st_size
            p.unlink()
        except OSError:
            pass
    return freed


def donor_size_mb(mod_id: int) -> float:
    """Размер самого большого файла донора в мегабайтах, 0 — если не удалось узнать."""
    try:
        files = _get(f"/api/nexus/mods/{mod_id}/files?categories=main,update",
                     timeout=180) or {}
    except Exception:
        return 0.0
    best = 0
    for f in (files.get("files") or files.get("results") or []):
        size = f.get("size_in_bytes") or (f.get("size") or 0) * 1024
        best = max(best, int(size or 0))
    return best / 2**20


def mods_by_size(limit: int) -> list:
    con = sqlite3.connect(str(ROOT / "cache" / "translations.db"), timeout=180)
    con.execute("PRAGMA busy_timeout=180000")
    rows = con.execute(
        "SELECT mod_name, COUNT(*) n FROM strings "
        "WHERE TRIM(COALESCE(translation,'')) <> '' "
        "GROUP BY mod_name ORDER BY n DESC").fetchall()
    con.close()
    return rows[:limit]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=20, help="сколько крупнейших модов взять")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--language", default="Russian")
    ap.add_argument("--skip", action="append", default=[])
    # Повторный проход по тем, у кого донора не нашлось прошлым, более слабым поиском.
    # Перебирать все 1 945 незачем: у кого донор уже есть, тем он и остаётся.
    ap.add_argument("--only-file", help="файл со списком модов, по одному на строку")
    # Донор — источник текста, а не дистрибутив. Сорок килобайт перевода внутри
    # четырёхсотмегабайтного архива патч-хаба стоят дороже, чем дают: архив всё равно
    # качается целиком, транзитом через диск. Порог отсекает такие, и они остаются
    # доступны поимённо, когда действительно нужны.
    ap.add_argument("--max-mb", type=float, default=150.0,
                    help="не брать доноров тяжелее этого (0 — без порога)")
    args = ap.parse_args()

    donors_dir = ROOT / "cache" / "donors"
    donors_dir.mkdir(parents=True, exist_ok=True)
    freed = sweep_parts(donors_dir)
    if freed:
        print(f"убрано недокачанных хвостов: {freed / 2**20:,.0f} МБ\n",
              file=out, flush=True)

    mods = [(m, n) for m, n in mods_by_size(args.top) if m not in args.skip]
    if args.only_file:
        want = {ln.strip() for ln in Path(args.only_file).read_text(
            encoding="utf-8").splitlines() if ln.strip()}
        mods = [(m, n) for m, n in mods if m in want]
    print(f"модов к обходу: {len(mods)}   строк в них: {sum(n for _, n in mods):,}\n",
          file=out, flush=True)

    totals = {"донор найден": 0, "донора нет": 0, "не скачалось": 0,
              "заполнено пустых": 0, "подтверждено игрой": 0, "применено": 0}
    for mod, n in mods:
        q = urllib.parse.urlencode({"mod": mod, "language": args.language})
        try:
            donors = (_get(f"/api/nexus/donors?{q}") or {}).get("results") or []
        except Exception as exc:
            print(f"  {mod[:44]:<44} поиск не удался: {str(exc)[:50]}", file=out, flush=True)
            continue
        if not donors:
            totals["донора нет"] += 1
            print(f"  {mod[:44]:<44} {n:>7,}  донора нет", file=out, flush=True)
            continue
        totals["донор найден"] += 1
        # Самый скачиваемый — не гарантия качества, но лучший доступный признак того,
        # что перевод живой и кто-то его проверял.
        best = max(donors, key=lambda d: d.get("downloads") or 0)
        if args.max_mb:
            mb = donor_size_mb(best.get("mod_id"))
            if mb > args.max_mb:
                totals["слишком тяжёлый"] = totals.get("слишком тяжёлый", 0) + 1
                print(f"  {mod[:44]:<44} {n:>7,}  донор {mb:,.0f} МБ — пропускаю "
                      f"(порог {args.max_mb:,.0f})", file=out, flush=True)
                continue
        t0 = time.time()
        try:
            rep = _post("/api/nexus/transfer/plan",
                        {"mod": mod, "donor_mod_id": best.get("mod_id"),
                         "language": args.language})
        except Exception as exc:
            totals["не скачалось"] += 1
            print(f"  {mod[:44]:<44} {n:>7,}  донор не скачался: {str(exc)[:46]}",
                  file=out, flush=True)
            continue
        plan = rep.get("plan") or {}
        counts = plan.get("counts") or {}
        line = (f"  {mod[:44]:<44} {n:>7,}  донор #{best.get('mod_id')}  "
                f"пусто {counts.get('fill', 0):>5}  расх. {counts.get('conflict', 0):>6}")
        if not args.write:
            print(line + "   (сухой прогон)", file=out, flush=True)
            continue
        try:
            res = _post("/api/nexus/transfer/apply",
                        {"plan_id": rep.get("plan_id"), "confirmed_only": True,
                         "status": "needs_review"})
        except Exception as exc:
            print(line + f"   применить не вышло: {str(exc)[:40]}", file=out, flush=True)
            continue
        applied = res.get("applied") or 0
        conf = res.get("confirmed_by_official") or 0
        totals["заполнено пустых"] += counts.get("fill", 0)
        totals["подтверждено игрой"] += conf
        totals["применено"] += applied
        print(line + f"  → применено {applied:>5} (из них подтверждено игрой {conf:>4})"
                     f"  {time.time()-t0:.0f}с", file=out, flush=True)

    print("\nитого:", file=out)
    for k, v in totals.items():
        print(f"  {k:<22}{v:>8,}", file=out)
    if not args.write:
        print("\nсухой прогон — добавьте --write", file=out)


if __name__ == "__main__":
    main()
