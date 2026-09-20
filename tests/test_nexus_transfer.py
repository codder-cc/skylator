"""Translate From Mod — unpacking, harvesting and the merge.

The merge is the part worth testing hardest: it writes into the translation store, and
the ways it can be wrong are all quiet ones. Folding in a donor that turns out not to be
translated would replace finished Russian with the English it came from and report a
large successful merge; matching the wrong record would put a real translation on the
wrong string. Both look like success from the outside.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from translator.db.database import TranslationDB
from translator.db.repo import StringRepo
from translator.nexus import merge as merge_mod
from translator.nexus.archive import ArchiveError, extract, find_7z
from translator.nexus.harvest import DonorSource, DonorString, string_key, table_stem
from translator.nexus.merge import CONFLICT, FILL, REJECTED, SAME, UNMATCHED


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def repo(tmp_path):
    db = TranslationDB(tmp_path / "t.db")
    return StringRepo(db)


def _key(form_id="01000800", rec="WEAP", field="FULL", idx=0, vmad=0):
    return string_key(form_id, rec, field, idx, vmad)


def _seed(repo, mod="TestMod", esp="Test.esp", rows=()):
    """rows: (form_id, original, translation, status)"""
    for form_id, original, translation, status in rows:
        repo.upsert(mod_name=mod, esp_name=esp, key=_key(form_id),
                    original=original, translation=translation, status=status,
                    form_id=form_id, rec_type="WEAP", field_type="FULL", field_index=0)


def _donor(esp="Test.esp", items=()):
    """A donor plugin. items: (form_id, text)"""
    return [DonorSource(path=Path(esp), name=esp, kind="esp", strings=[
        DonorString(kind="esp", match_id=("esp", esp.lower(), _key(f)), text=t,
                    origin=esp, esp_name=esp, key=_key(f), form_id=f,
                    rec_type="WEAP", field_type="FULL", field_index=0)
        for f, t in items])]


def _mcm_donor(name="SkyUI_SE_russian.txt", items=(), kind="mcm"):
    """A donor MCM table. items: ($KEY, text)"""
    stem = table_stem(name)
    return [DonorSource(path=Path(name), name=name, kind=kind, strings=[
        DonorString(kind=kind, match_id=(kind, stem, k), text=t, origin=name)
        for k, t in items])]


def _swf_donor(name="map.swf", items=()):
    """A donor SWF. items: (character id, text)"""
    return [DonorSource(path=Path(name), name=name, kind="swf", strings=[
        DonorString(kind="swf", match_id=("swf", name.lower(), c), text=t, origin=name)
        for c, t in items])]


def _seed_mcm(repo, mod="TestMod", rel="interface/translations/SkyUI_SE_english.txt",
              rows=()):
    """rows: (line_idx, $KEY, original, translation, status)"""
    for idx, mcm_key, original, translation, status in rows:
        repo.upsert(mod_name=mod, esp_name=Path(rel).name,
                    key=f"mcm:{rel}:{idx}:{mcm_key}", original=original,
                    translation=translation, status=status,
                    form_id=mcm_key, rec_type="MCM", field_type="TEXT", field_index=idx)


def _seed_swf(repo, mod="TestMod", rel="interface/map.swf", rows=()):
    """rows: (chid, original, translation, status)"""
    for chid, original, translation, status in rows:
        repo.upsert(mod_name=mod, esp_name=Path(rel).name,
                    key=f"swf:{rel}:{chid}", original=original,
                    translation=translation, status=status,
                    form_id=chid, rec_type="SWF", field_type="TEXT", field_index=0)


# ── archive ───────────────────────────────────────────────────────────────────


def test_a_zip_extracts_and_is_counted(tmp_path):
    src = tmp_path / "m.zip"
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("Data/Test.esp", b"TES4" + b"\0" * 60)
        zf.writestr("readme.txt", "hello")
    out = extract(src, tmp_path / "out")
    assert out.files == 2 and out.bytes > 0
    assert (tmp_path / "out" / "Data" / "Test.esp").exists()


def test_only_the_wanted_suffixes_are_written(tmp_path):
    # A donor is often a 400 MB upload whose translated text is 40 kB of it. Writing
    # the rest to disk to read past it is waste nobody notices until the disk is full.
    src = tmp_path / "m.zip"
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("Test.esp", b"TES4")
        zf.writestr("textures/huge.dds", b"x" * 5000)
    out = extract(src, tmp_path / "out", only_suffixes=(".esp",))
    assert out.files == 1
    assert not (tmp_path / "out" / "textures").exists()


@pytest.mark.skipif(find_7z() is not None,
                    reason="7-Zip present, so the no-extractor path cannot be reached")
def test_a_7z_without_7zip_fails_with_an_actionable_message(tmp_path):
    src = tmp_path / "m.7z"
    src.write_bytes(b"7z\xbc\xaf\x27\x1c")
    with pytest.raises(ArchiveError) as e:
        extract(src, tmp_path / "out")
    assert "archive_tool_path" in str(e.value)


def test_a_missing_archive_says_so(tmp_path):
    with pytest.raises(ArchiveError):
        extract(tmp_path / "nope.7z", tmp_path / "out")


def test_a_zip_entry_escaping_the_destination_is_refused(tmp_path):
    # An archive is untrusted input, and a "../../" entry is a convenient way to
    # overwrite a config file on the machine unpacking it.
    src = tmp_path / "evil.zip"
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("../escaped.txt", "pwned")
    dest = tmp_path / "out"
    extract(src, dest, only_suffixes=(".txt",))
    assert not (tmp_path / "escaped.txt").exists()


# ── language guard ────────────────────────────────────────────────────────────


def test_an_untranslated_donor_string_is_rejected():
    # Some "translation" uploads ship the untouched English plugin beside the translated
    # one. Folding that in overwrites real translations with the original text.
    ok, why = merge_mod.looks_translated("Iron Sword", "Iron Sword", "Russian")
    assert not ok and "identical" in why

    ok, why = merge_mod.looks_translated("Steel Sword", "Iron Sword", "Russian")
    assert not ok and "Russian" in why

    assert merge_mod.looks_translated("Железный меч", "Iron Sword", "Russian")[0]


def test_a_string_with_nothing_to_translate_passes():
    # "100%" is a legitimate translation of "100%" and carries no letters to check.
    assert merge_mod.looks_translated("100%", "100%", "Russian")[0] is False   # identical
    assert merge_mod.looks_translated("50/50", "100%", "Russian")[0] is True


def test_an_unknown_target_language_falls_back_to_being_different():
    ok, _ = merge_mod.looks_translated("Zwaard", "Sword", "Klingon")
    assert ok


# ── planning ──────────────────────────────────────────────────────────────────


def test_a_donor_fills_what_we_have_nothing_for(repo):
    _seed(repo, rows=[("01000800", "Iron Sword", "", "pending")])
    plan = merge_mod.plan(repo, "TestMod", _donor(items=[("01000800", "Железный меч")]))
    assert plan.counts == {FILL: 1}
    c = plan.candidates[0]
    assert c.match == "exact" and c.donor_text == "Железный меч"


def test_a_different_existing_translation_is_a_conflict_not_a_fill(repo):
    _seed(repo, rows=[("01000800", "Iron Sword", "Стальной меч", "translated")])
    plan = merge_mod.plan(repo, "TestMod", _donor(items=[("01000800", "Железный меч")]))
    assert plan.counts == {CONFLICT: 1}
    assert plan.candidates[0].current == "Стальной меч"


def test_an_identical_translation_is_nothing_to_do(repo):
    _seed(repo, rows=[("01000800", "Iron Sword", "Железный меч", "translated")])
    plan = merge_mod.plan(repo, "TestMod", _donor(items=[("01000800", "Железный меч")]))
    assert plan.counts == {SAME: 1}


def test_a_donor_record_we_do_not_have_is_unmatched(repo):
    _seed(repo, rows=[("01000800", "Iron Sword", "", "pending")])
    plan = merge_mod.plan(repo, "TestMod", _donor(items=[("01000999", "Что-то ещё")]))
    assert plan.counts == {UNMATCHED: 1}


def test_an_english_donor_is_rejected_rather_than_applied(repo):
    _seed(repo, rows=[("01000800", "Iron Sword", "Железный меч", "translated")])
    plan = merge_mod.plan(repo, "TestMod", _donor(items=[("01000800", "Iron Sword")]))
    assert plan.counts == {REJECTED: 1}
    assert plan.usable == 0


def test_a_shifted_master_index_still_matches(repo):
    # The high byte of a FormID indexes the plugin's own master list. It is stable in a
    # file, but a donor that added a master shifts it — and every record would miss.
    _seed(repo, rows=[("01000800", "Iron Sword", "", "pending")])
    plan = merge_mod.plan(repo, "TestMod", _donor(items=[("02000800", "Железный меч")]))
    assert plan.counts == {FILL: 1}
    assert plan.candidates[0].match == "local_id"


def test_an_ambiguous_local_id_is_not_guessed(repo):
    # Two of our records collide once the master byte is masked. Taking either would be
    # a guess, and a guess written into the store is indistinguishable from a fact.
    _seed(repo, rows=[("01000800", "Iron Sword", "", "pending"),
                      ("03000800", "Iron Dagger", "", "pending")])
    plan = merge_mod.plan(repo, "TestMod", _donor(items=[("02000800", "Железный меч")]))
    assert plan.counts == {UNMATCHED: 1}


def test_local_id_matching_can_be_switched_off(repo):
    _seed(repo, rows=[("01000800", "Iron Sword", "", "pending")])
    plan = merge_mod.plan(repo, "TestMod", _donor(items=[("02000800", "Железный меч")]),
                          allow_local_id=False)
    assert plan.counts == {UNMATCHED: 1}


def test_a_renamed_donor_plugin_can_be_mapped_onto_ours(repo):
    _seed(repo, esp="Ours.esp", rows=[("01000800", "Iron Sword", "", "pending")])
    plan = merge_mod.plan(repo, "TestMod",
                          _donor(esp="TheirsRU.esp", items=[("01000800", "Железный меч")]),
                          esp_map={"TheirsRU.esp": "Ours.esp"})
    assert plan.counts == {FILL: 1}


# ── MCM and SWF ───────────────────────────────────────────────────────────────


def test_an_mcm_table_matches_by_its_key_not_its_line_number(repo):
    # Translators reorder and re-comment these files freely. Matching by position would
    # miss almost everything while looking like it worked.
    _seed_mcm(repo, rows=[(0, "$General", "General", "", "pending"),
                          (1, "$Advanced", "Advanced", "", "pending")])
    plan = merge_mod.plan(repo, "TestMod", _mcm_donor(items=[
        ("$Advanced", "Дополнительно"), ("$General", "Общие")]))
    assert plan.counts == {FILL: 2}
    by_text = {c.original: c.donor_text for c in plan.candidates}
    assert by_text == {"General": "Общие", "Advanced": "Дополнительно"}


def test_an_mcm_table_matches_across_its_language_suffix(repo):
    # Ours came from SkyUI_SE_english.txt, the donor ships SkyUI_SE_russian.txt.
    _seed_mcm(repo, rows=[(0, "$General", "General", "", "pending")])
    plan = merge_mod.plan(repo, "TestMod",
                          _mcm_donor("SkyUI_SE_russian.txt", [("$General", "Общие")]))
    assert plan.counts == {FILL: 1}


def test_a_russian_table_shipped_as_english_txt_still_matches(repo):
    # Plenty of Russian mods put their text in _english.txt on purpose, so the game
    # shows it without the player changing the language setting.
    _seed_mcm(repo, rows=[(0, "$General", "General", "", "pending")])
    plan = merge_mod.plan(repo, "TestMod",
                          _mcm_donor("SkyUI_SE_english.txt", [("$General", "Общие")]))
    assert plan.counts == {FILL: 1}


def test_an_mcm_table_from_a_different_mod_does_not_match(repo):
    _seed_mcm(repo, rows=[(0, "$General", "General", "", "pending")])
    plan = merge_mod.plan(repo, "TestMod",
                          _mcm_donor("Frostfall_russian.txt", [("$General", "Общие")]))
    assert plan.counts == {UNMATCHED: 1}


def test_swf_text_matches_by_character_id(repo):
    # The path inside the mod differs between uploads; the character ids do not.
    _seed_swf(repo, rel="interface/map.swf", rows=[("42", "Continue", "", "pending")])
    plan = merge_mod.plan(repo, "TestMod", _swf_donor("map.swf", [("42", "Продолжить")]))
    assert plan.counts == {FILL: 1}
    assert plan.candidates[0].kind == "swf"


def test_the_plan_reports_where_the_gain_came_from(repo):
    _seed(repo, rows=[("01000800", "Iron Sword", "", "pending")])
    _seed_mcm(repo, rows=[(0, "$General", "General", "", "pending")])
    plan = merge_mod.plan(repo, "TestMod",
                          _donor(items=[("01000800", "Железный меч")])
                          + _mcm_donor(items=[("$General", "Общие")]))
    assert plan.counts_by_kind == {"esp": {FILL: 1}, "mcm": {FILL: 1}}


def test_mcm_and_plugin_rows_do_not_collide(repo):
    # Both encodings live in one `key` column; an indexer that did not tell them apart
    # would let an MCM key shadow a plugin record.
    _seed(repo, rows=[("01000800", "Iron Sword", "", "pending")])
    _seed_mcm(repo, rows=[(0, "$General", "General", "", "pending")])
    plan = merge_mod.plan(repo, "TestMod", _donor(items=[("01000800", "Железный меч")]))
    assert plan.counts == {FILL: 1}
    assert plan.candidates[0].original == "Iron Sword"


# ── applying ──────────────────────────────────────────────────────────────────


def test_apply_writes_fills_and_leaves_conflicts_alone_by_default(repo):
    _seed(repo, rows=[("01000800", "Iron Sword", "", "pending"),
                      ("01000801", "Steel Sword", "Старый перевод", "translated")])
    plan = merge_mod.plan(repo, "TestMod", _donor(items=[
        ("01000800", "Железный меч"), ("01000801", "Стальной меч")]))
    out = merge_mod.apply(repo, plan)

    assert out["applied"] == 1 and out["skipped"] == 1
    rows = {r["form_id"]: r for r in repo.get_all_strings("TestMod")}
    assert rows["01000800"]["translation"] == "Железный меч"
    # A donor is a second opinion, not an authority.
    assert rows["01000801"]["translation"] == "Старый перевод"


def test_apply_lands_as_review_rather_than_as_accepted(repo):
    _seed(repo, rows=[("01000800", "Iron Sword", "", "pending")])
    plan = merge_mod.plan(repo, "TestMod", _donor(items=[("01000800", "Железный меч")]))
    merge_mod.apply(repo, plan)
    assert repo.get_all_strings("TestMod")[0]["status"] == "needs_review"


def test_apply_can_accept_outright_when_asked(repo):
    _seed(repo, rows=[("01000800", "Iron Sword", "", "pending")])
    plan = merge_mod.plan(repo, "TestMod", _donor(items=[("01000800", "Железный меч")]))
    merge_mod.apply(repo, plan, status="translated")
    assert repo.get_all_strings("TestMod")[0]["status"] == "translated"


def test_overwrite_takes_the_conflicts_too(repo):
    _seed(repo, rows=[("01000800", "Iron Sword", "Старый перевод", "translated")])
    plan = merge_mod.plan(repo, "TestMod", _donor(items=[("01000800", "Железный меч")]))
    out = merge_mod.apply(repo, plan, overwrite=True)
    assert out["applied"] == 1
    assert repo.get_all_strings("TestMod")[0]["translation"] == "Железный меч"


def test_only_the_ticked_rows_are_written(repo):
    _seed(repo, rows=[("01000800", "Iron Sword", "", "pending"),
                      ("01000801", "Steel Sword", "", "pending")])
    plan = merge_mod.plan(repo, "TestMod", _donor(items=[
        ("01000800", "Железный меч"), ("01000801", "Стальной меч")]))
    out = merge_mod.apply(repo, plan, only_keys=[("Test.esp", _key("01000800"))])

    assert out["applied"] == 1
    rows = {r["form_id"]: r["translation"] for r in repo.get_all_strings("TestMod")}
    assert rows["01000800"] == "Железный меч" and rows["01000801"] == ""


def test_ticked_rows_arrive_from_json_as_lists(repo):
    # Тест выше передаёт кортежи и потому ничего не проверял: JSON кортежей не знает, и
    # интерфейс присылает ["esp", "key"]. На них `set(only_keys)` падал с «unhashable
    # type: 'list'» — то есть форма запроса, которую документирует /transfer/apply,
    # не работала вовсе.
    _seed(repo, rows=[("01000800", "Iron Sword", "", "pending"),
                      ("01000801", "Steel Sword", "", "pending")])
    plan = merge_mod.plan(repo, "TestMod", _donor(items=[
        ("01000800", "Железный меч"), ("01000801", "Стальной меч")]))
    out = merge_mod.apply(repo, plan, only_keys=[["Test.esp", _key("01000800")]])

    assert out["applied"] == 1
    rows = {r["form_id"]: r["translation"] for r in repo.get_all_strings("TestMod")}
    assert rows["01000800"] == "Железный меч" and rows["01000801"] == ""


def test_a_malformed_ticked_row_says_so_instead_of_writing_nothing(repo):
    # Пропустить молча — значит вернуть «применено 0» без причины, и искать её будет
    # человек, глядя на пустой результат.
    _seed(repo, rows=[("01000800", "Iron Sword", "", "pending")])
    plan = merge_mod.plan(repo, "TestMod", _donor(items=[("01000800", "Железный меч")]))
    with pytest.raises(ValueError, match="only_keys"):
        merge_mod.apply(repo, plan, only_keys=["Test.esp"])


def test_an_applied_row_records_where_it_came_from(repo):
    # A translation taken from someone else's mod must never be mistaken later for our
    # own output — not in a quality report, and not in a training set.
    _seed(repo, rows=[("01000800", "Iron Sword", "", "pending")])
    plan = merge_mod.plan(repo, "TestMod", _donor(items=[("01000800", "Железный меч")]))
    merge_mod.apply(repo, plan)
    assert repo.get_all_strings("TestMod")[0]["source"] == "nexus-translation"


def test_the_pair_is_offered_to_the_cross_mod_dictionary(repo):
    class _Dict:
        def __init__(self):
            self.pairs, self.saved = [], 0

        def add(self, original, translation):
            self.pairs.append((original, translation))

        def save(self):
            self.saved += 1

    gd = _Dict()
    _seed(repo, rows=[("01000800", "Iron Sword", "", "pending")])
    plan = merge_mod.plan(repo, "TestMod", _donor(items=[("01000800", "Железный меч")]))
    merge_mod.apply(repo, plan, global_dict=gd)

    assert gd.pairs == [("Iron Sword", "Железный меч")] and gd.saved == 1


def test_apply_refuses_a_status_the_store_does_not_use(repo):
    plan = merge_mod.plan(repo, "TestMod", _donor(items=[]))
    with pytest.raises(ValueError):
        merge_mod.apply(repo, plan, status="definitely-fine")


def test_planning_writes_nothing(repo):
    _seed(repo, rows=[("01000800", "Iron Sword", "", "pending")])
    merge_mod.plan(repo, "TestMod", _donor(items=[("01000800", "Железный меч")]))
    assert repo.get_all_strings("TestMod")[0]["translation"] == ""


def test_a_packed_table_matches_a_loose_one(repo):
    # Where an MCM table physically sits is packaging, not identity. Our copy of
    # A Matter of Time keeps its table inside a .bsa and the published Russian
    # translation ships the same table loose; keying on the container made all 133 of
    # its strings unmatched while looking like the donor simply had nothing to offer.
    _seed_mcm(repo, rel="interface/translations/amatteroftime_english.txt",
              rows=[(0, "$AMOT Page General", "General", "", "pending")])
    # Restate ours in the packed form, leaving the donor loose.
    repo.db.execute(
        "UPDATE strings SET key = ?, esp_name = ?",
        ("bsa-mcm:AMOT.bsa:interface/translations/amatteroftime_english.txt"
         ":0:$AMOT Page General", "AMOT.bsa/amatteroftime_english.txt"))
    repo.db.commit()

    plan = merge_mod.plan(repo, "TestMod",
                          _mcm_donor("amatteroftime_russian.txt",
                                     [("$AMOT Page General", "Общие")]))
    assert plan.counts == {FILL: 1}


def test_a_loose_table_wins_over_the_packed_copy(repo):
    # Skyrim reads a loose table over the one in the archive, so that is the row a
    # translation must land on. Without this the winner is whichever row the query
    # happened to return last.
    _seed_mcm(repo, rel="interface/translations/x_english.txt",
              rows=[(0, "$K", "Loose original", "", "pending")])
    repo.upsert(mod_name="TestMod", esp_name="X.bsa/x_english.txt",
                key="bsa-mcm:X.bsa:interface/translations/x_english.txt:0:$K",
                original="Packed original", translation="", status="pending",
                form_id="$K", rec_type="BSA-MCM", field_type="TEXT", field_index=0)

    plan = merge_mod.plan(repo, "TestMod", _mcm_donor("x_russian.txt", [("$K", "Текст")]))
    assert plan.counts == {FILL: 1}
    assert plan.candidates[0].original == "Loose original"


# ── вторая инстанция ──────────────────────────────────────────────────────────


OFFICIAL = {"Bleak Falls Barrow": "Ветреный пик", "Soul Cairn": "Каирн Душ",
            "The Bannered Mare": "Гарцующая кобыла", "Iron Sword": "Железный меч",
            "To Place": "ПОМЕСТИТЬ", "The Cause": "Великое дело"}


def test_a_conflict_the_game_itself_settles_is_singled_out(repo):
    # Донор — мнение, и своё мнение он проигрывает нашему 90 раз из 2 031. Но когда имя
    # написал и он, и сама игра, а мы нет — против машины стоят две независимые
    # инстанции, и решает их совпадение, а не донор.
    _seed(repo, rows=[("01000800", "Meet me at Bleak Falls Barrow tonight.",
                       "Встретимся у Кургана Блек Фоллс сегодня.", "translated")])
    plan = merge_mod.plan(repo, "TestMod", _donor(items=[
        ("01000800", "Встретимся у Ветреного пика сегодня.")]))
    got = merge_mod.confirmed_by_official(plan, OFFICIAL)
    assert len(got) == 1 and got[0][2] == "Ветреный пик"


def test_a_conflict_where_we_already_use_the_official_name_is_left_alone(repo):
    _seed(repo, rows=[("01000800", "Meet me at Bleak Falls Barrow tonight.",
                       "Встретимся у Ветреного пика вечером.", "translated")])
    plan = merge_mod.plan(repo, "TestMod", _donor(items=[
        ("01000800", "Увидимся у Ветреного пика вечером.")]))
    assert merge_mod.confirmed_by_official(plan, OFFICIAL) == []


def test_a_conflict_the_donor_gets_wrong_too_is_not_confirmed(repo):
    # Донор не лучше нас — применять нечего.
    _seed(repo, rows=[("01000800", "Meet me at Bleak Falls Barrow tonight.",
                       "Встретимся у Кургана Блек Фоллс.", "translated")])
    plan = merge_mod.plan(repo, "TestMod", _donor(items=[
        ("01000800", "Встретимся у Холодного Кургана.")]))
    assert merge_mod.confirmed_by_official(plan, OFFICIAL) == []


def test_a_whole_string_name_is_not_this_rule_s_business(repo):
    # Строку, которая ЦЕЛИКОМ есть в таблице, судят ворота записи, а не эта проверка.
    _seed(repo, rows=[("01000800", "Bleak Falls Barrow", "Курган Блек Фоллс", "translated")])
    plan = merge_mod.plan(repo, "TestMod", _donor(items=[("01000800", "Ветреный пик")]))
    assert merge_mod.confirmed_by_official(plan, OFFICIAL) == []


def test_a_button_label_is_not_an_entity():
    # «To Place» и «The Cause» лежат в таблице игры как кнопка и реплика; внутри чужой
    # фразы это обычные слова, и на них первая версия проверки набрала ложный урожай.
    table = merge_mod.entity_table(OFFICIAL)
    assert "to place" not in table and "the cause" not in table
    assert "bleak falls barrow" in table and "soul cairn" in table
