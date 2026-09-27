"""Фиксированный набор строк для золотого сравнения промптов — без импортов агента.

Модуль нарочно ничего не импортирует из remote_worker: генератор золотого файла
подставляет сюда классы из ДРУГОЙ ревизии кода (той, что была до режима сцены), а тест
— из нынешней. Сравнивается то, что ушло в модель, байт в байт.

Строки несут поля scene/scene_pos, но пакет — без флага scene. Ровно это и должно быть
неотличимо от прежнего: поля без флага не значат ничего.
"""
from __future__ import annotations

import asyncio
import json
import re
from types import SimpleNamespace as NS

AID = "golden-assignment"


def items() -> list[dict]:
    def one(sid, original, rec, field, mod="GoldMod", esp="Gold.esp", **kw):
        d = dict(id=sid, key=f"('0500{sid:04X}', '{rec}', '{field}', 0, 0)", esp=esp,
                 mod_name=mod, original=original, rec_type=rec, field_type=field)
        d.update(kw)
        return d

    npc = "Speaker: Eldawyn (female), High Elf\nUse the speaker's gender for first-person past-tense forms (feminine)."
    to_npc = ("The player is speaking TO: Eldawyn (female), High Elf\n"
              "This line is addressed to that character, so second-person forms take their gender (feminine).")
    return [
        one(1, "I know this place and remember the old road.", "INFO", "NAM1", speaker=npc,
            talk='the player says: "What do you know about the road?"',
            entities="Names the game already has: Whiterun = Вайтран",
            style="INFO_STYLE", scene="gold.esp:000010", scene_pos=1),
        one(2, "It leads to the barrow, past Whiterun.", "INFO", "NAM1", speaker=npc,
            talk='just before, they said: "I know this place."', style="INFO_STYLE",
            scene="gold.esp:000010", scene_pos=2),
        one(3, "What do you know about the road?", "DIAL", "FULL", speaker=to_npc,
            talk='the character answers: "I know this place."',
            scene="gold.esp:000010", scene_pos=0),
        one(4, "The book describes a distant village and the people who lived there "
               "long before the war.", "BOOK", "DESC", style="BOOK_STYLE"),
        one(5, "Iron Sword", "WEAP", "FULL", mod="OtherMod", esp="Other.esp"),
        one(6, "Steel Dagger", "WEAP", "FULL", mod="OtherMod", esp="Other.esp"),
        one(7, "You brute. A good vintage wine is wasted on your tongue.", "INFO", "NAM1",
            mod="OtherMod", esp="Other.esp", entities="Names: Eldawyn = Элдавин",
            current="Ты грубиян.", scene="other.esp:000020", scene_pos=1),
        one(8, "Fortifies health while in Whiterun.", "MGEF", "DNAM"),
        one(9, "Hello.", "INFO", "NAM1", speaker=npc),
    ]


def package(extra: dict | None = None) -> dict:
    return dict(offline_job_id=AID, host_job_id="golden-job", mod_name="2 mods",
                strings=items(), context="",
                mods_context={"GoldMod": "GOLD_MOD_SUMMARY", "OtherMod": "OTHER_MOD_SUMMARY"},
                src_lang="English", tgt_lang="Russian",
                params={"batch_size": 8, "max_tokens": 256},
                terminology="Key terms:\n  Whiterun = Вайтран\n", preserve_tokens=[],
                tm_pairs={"Whiterun": "Вайтран", "Sword": "Меч"},
                **(extra or {}))


_NUMBERED = re.compile(r"^(\d+)\. ", re.M)


class CountingBackend:
    """Отвечает столько пунктов, сколько строк в промпте — чтобы не было повторов."""

    def __init__(self):
        self.calls: list[str] = []
        self.last_finish_reason = "stop"

    def _infer(self, prompt, params=None, stop_check=None):
        self.calls.append(prompt)
        tail = prompt.rsplit("Strings", 1)[-1].split("<|im_end|>", 1)[0]
        n = len(_NUMBERED.findall(tail))
        return "\n".join(f"{k}. Перевод строки номер {k}." for k in range(1, n + 1))


def run(store_cls, runner_cls, persist, db_path, extra: dict | None = None) -> list[str]:
    """Пакет → хранилище → «перезапущенный» агент → промпты, ушедшие в бэкенд."""
    store = store_cls(db_path)
    persist(NS(result_store=store), json.loads(json.dumps(package(extra))))
    store.close()
    store = store_cls(db_path)          # как после перезапуска: всё — из базы
    try:
        meta = json.loads(store.get_assignment(AID)["params_json"])
        backend = CountingBackend()

        async def go():
            await runner_cls(store, AID, meta).run(NS(backend=backend),
                                                   asyncio.get_running_loop())
        asyncio.run(go())
        return backend.calls
    finally:
        store.close()
