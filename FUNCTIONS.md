# Skylator — пофункциональный разбор

Каждая функция и метод кодовой базы на коммите `390e72d`. Всего **972** вызываемых объектов в 121 модуле.

Статус определён объективно: покрытие получено прогоном всех 463 тестов под `coverage.py`, ссылки — подсчётом обращений по всему репозиторию, включая frontend.

| Статус | Значение |
|---|---|
| 🟢 покрыт | ≥60% строк тела исполняется тестами |
| 🟡 частично | 12–60% |
| 🟠 не покрыт | <12%, но код вызывается из проекта |
| 🔴 мёртвый | не вызывается ниоткуда и не вызывается фреймворком |
| ⚪ не измерен | вне охвата coverage |

## Сводка

| Статус | Кол-во | Доля |
|---|---|---|
| 🟠 не покрыт | 422 | 43% |
| 🟡 частично | 303 | 31% |
| 🟢 покрыт | 232 | 24% |
| 🔴 мёртвый | 15 | 2% |
| **Всего** | **972** | |

## По категориям

| Категория | Всего | 🟢 | 🟡 | 🟠 | 🔴 |
|---|---|---|---|---|---|
| БД и хранилище | 42 | 9 | 26 | 5 | 2 |
| Управление строками | 25 | 7 | 14 | 3 | 1 |
| Качество и валидация | 11 | 7 | 3 | 1 | 0 |
| Промпты и парсинг ответа | 21 | 7 | 11 | 2 | 1 |
| Контекст | 27 | 1 | 12 | 13 | 1 |
| Пайплайны перевода | 18 | 0 | 0 | 18 | 0 |
| Ансамбль моделей | 11 | 0 | 0 | 10 | 1 |
| Модели (host) | 42 | 1 | 17 | 23 | 1 |
| Парсинг форматов | 17 | 0 | 0 | 17 | 0 |
| Движки файлов (scripts) | 85 | 38 | 20 | 26 | 1 |
| Джобы и оркестрация | 88 | 47 | 30 | 10 | 1 |
| Реестр воркеров и wire | 58 | 41 | 14 | 3 | 0 |
| Оффлайн-диспатч | 24 | 8 | 14 | 2 | 0 |
| Управление моделями фло­та | 33 | 18 | 10 | 4 | 1 |
| Резервирование работы | 6 | 1 | 5 | 0 | 0 |
| Статистика и оценки | 16 | 2 | 11 | 3 | 0 |
| Кэши | 27 | 6 | 15 | 5 | 1 |
| Сканер модов | 22 | 0 | 7 | 15 | 0 |
| HTTP-маршруты | 202 | 15 | 52 | 133 | 2 |
| Приложение и конфиг | 25 | 3 | 8 | 14 | 0 |
| Remote (host-side client) | 45 | 0 | 8 | 35 | 2 |
| Агент: сервер | 63 | 10 | 3 | 50 | 0 |
| Агент: модели | 30 | 0 | 2 | 28 | 0 |
| Агент: durability | 34 | 11 | 21 | 2 | 0 |


### БД и хранилище

| Функция / метод | Файл:строка | loc | Покрытие | Ссылок | Статус | Назначение |
|---|---|---|---|---|---|---|
| `TranslationDB.__init__` | `database.py:20` | 5 | 100% | 75 | 🟢 покрыт |  |
| `TranslationDB._connect` | `database.py:26` | 12 | 58% | 21 | 🟡 частично | Return a thread-local connection, creating it if needed. |
| `TranslationDB._init_schema` | `database.py:39` | 8 | 88% | 3 | 🟢 покрыт | Create tables and indexes if they don't exist, then run migrations. |
| `TranslationDB.execute` | `database.py:48` | 2 | 100% | 213 | 🟢 покрыт |  |
| `TranslationDB.executemany` | `database.py:51` | 2 | 100% | 14 | 🟢 покрыт |  |
| `TranslationDB.commit` | `database.py:54` | 2 | 100% | 119 | 🟢 покрыт |  |
| `TranslationDB.close` | `database.py:57` | 5 | 100% | 65 | 🟢 покрыт |  |
| `TranslationDB.is_empty` | `database.py:63` | 4 | 25% | 0 | 🔴 мёртвый | Return True if the strings table has no rows. |
| `TranslationDB.get_or_create_mod_id` | `database.py:68` | 11 | 9% | 1 | 🟠 не покрыт | Return the stable numeric ID for a mod folder, creating one if new. |
| `TranslationDB.get_mod_by_id` | `database.py:80` | 6 | 17% | 1 | 🟡 частично | Return folder_name for a mod ID, or None if unknown. |
| `TranslationDB.set_mod_priority` | `database.py:87` | 9 | 11% | 6 | 🟠 не покрыт | Set a mod's translation priority (higher = translated first by translate_all). |
| `TranslationDB.get_mod_priorities` | `database.py:97` | 4 | 25% | 4 | 🟡 частично | {folder_name: priority} for all mods with a row (default 0 elsewhere). |
| `TranslationDB.mod_row_count` | `database.py:102` | 5 | 20% | 1 | 🟡 частично |  |
| `TranslationDB.integrity_check` | `database.py:108` | 11 | 82% | 8 | 🟢 покрыт | Run PRAGMA integrity_check on this DB (or another file). True iff it reports 'ok'. |
| `TranslationDB.backup_to` | `database.py:120` | 22 | 46% | 5 | 🟡 частично | Atomically snapshot the whole DB to dest_path via VACUUM INTO. The master DB is the canoni |
| `TranslationDB.rotating_backup` | `database.py:143` | 22 | 59% | 3 | 🟡 частично | Write a *timestamped* integrity-verified snapshot and prune to the newest `keep`. Rotation |
| `import_all_trans_json` | `importer.py:14` | 34 | 0% | 2 | 🟠 не покрыт | Walk mods_dir and import all *.trans.json files into SQLite. Safe to call multiple times — |
| `start_background_import` | `importer.py:50` | 10 | 0% | 0 | 🔴 мёртвый | Start the import in a daemon thread. Returns the thread. |
| `MigrationRunner.run` | `migrations.py:227` | 39 | 51% | 190 | 🟡 частично |  |
| `StringRepo.__init__` | `repo.py:20` | 2 | 100% | 75 | 🟢 покрыт |  |
| `StringRepo.import_trans_json` | `repo.py:25` | 47 | 23% | 7 | 🟡 частично | Upsert a list of string dicts from a .trans.json file. Only updates translation/status/qua |
| `StringRepo.upsert` | `repo.py:75` | 34 | 15% | 23 | 🟡 частично |  |
| `StringRepo.bulk_insert_strings` | `repo.py:112` | 37 | 30% | 11 | 🟡 частично | Insert all strings from an ESP parse result into SQLite. Only inserts — does not overwrite |
| `StringRepo.esp_exists` | `repo.py:152` | 7 | 43% | 7 | 🟡 частично | Return True if any rows exist for this mod/esp combination. |
| `StringRepo.esp_string_count` | `repo.py:160` | 7 | 43% | 5 | 🟡 частично | Return the number of rows stored for this mod/esp combination. |
| `StringRepo.mod_has_data` | `repo.py:168` | 7 | 43% | 12 | 🟡 частично | Return True if SQLite has any rows for this mod. |
| `StringRepo.mod_stats` | `repo.py:178` | 18 | 22% | 6 | 🟡 частично | Return {total, translated, pending, needs_review} for a mod. |
| `StringRepo.all_mod_stats` | `repo.py:197` | 20 | 5% | 1 | 🟠 не покрыт | Return {mod_name: {total, translated, pending, needs_review}} for all mods. |
| `StringRepo.get_all_strings` | `repo.py:220` | 23 | 22% | 47 | 🟡 частично | Return all rows for a mod (no pagination). Used by apply_mod, recompute_scores, and transl |
| `StringRepo.get_strings` | `repo.py:246` | 80 | 38% | 9 | 🟡 частично | Paginated string query. Returns (rows, total_count). |
| `StringRepo.scope_counts` | `repo.py:327` | 29 | 14% | 40 | 🟡 частично | Return counts per scope + status tabs for a mod. |
| `StringRepo.get_rec_types` | `repo.py:357` | 9 | 11% | 2 | 🟠 не покрыт | Return distinct rec_type values for a mod (for the record-type filter). |
| `StringRepo.replace_in_translations` | `repo.py:367` | 27 | 52% | 5 | 🟡 частично | Bulk replace text in translation column. Returns count of rows changed. |
| `StringRepo.sync_duplicates` | `repo.py:395` | 17 | 35% | 5 | 🟡 частично | Apply translation to all strings with the same original text. Returns count changed. |
| `StringRepo.create_checkpoint` | `repo.py:415` | 35 | 34% | 14 | 🟡 частично | Snapshot current translation/status for a mod (or one ESP file). Returns checkpoint_id (UU |
| `StringRepo.restore_checkpoint` | `repo.py:451` | 30 | 23% | 6 | 🟡 частично | Restore strings to their state at the time of the checkpoint. Returns number of strings re |
| `StringRepo.delete_checkpoint` | `repo.py:482` | 7 | 57% | 6 | 🟡 частично |  |
| `StringRepo.list_checkpoints` | `repo.py:490` | 13 | 38% | 7 | 🟡 частично |  |
| `StringRepo.get_string_by_id` | `repo.py:506` | 5 | 60% | 5 | 🟢 покрыт |  |
| `StringRepo.get_history` | `repo.py:512` | 8 | 38% | 5 | 🟡 частично |  |
| `StringRepo.insert_history` | `repo.py:521` | 11 | 36% | 2 | 🟡 частично |  |
| `StringRepo.update_job_string_status` | `repo.py:533` | 8 | 50% | 3 | 🟡 частично |  |

### Управление строками

| Функция / метод | Файл:строка | loc | Покрытие | Ссылок | Статус | Назначение |
|---|---|---|---|---|---|---|
| `_sha256_hash` | `string_manager.py:36` | 3 | 67% | 32 | 🟢 покрыт | SHA256[:32] of text (16 bytes, negligible collision rate at ~2M strings). |
| `normalize_text` | `string_manager.py:45` | 6 | 33% | 17 | 🟡 частично | Conservative normalization for fuzzy reuse: collapse whitespace + casefold ONLY. (We delib |
| `_norm_hash` | `string_manager.py:53` | 2 | 100% | 1 | 🟢 покрыт |  |
| `StringManager.__init__` | `string_manager.py:60` | 9 | 44% | 75 | 🟡 частично | Args: repo: StringRepo instance mods_dir: Path to the mods directory (for ESP bootstrap) |
| `StringManager.save_string` | `string_manager.py:72` | 121 | 31% | 67 | 🟡 частично | Single write entry point for ALL string types. - Computes quality_score if not provided (s |
| `StringManager._ledger_write` | `string_manager.py:194` | 11 | 54% | 1 | 🟡 частично |  |
| `StringManager.bootstrap_esp` | `string_manager.py:208` | 26 | 4% | 4 | 🟠 не покрыт | Seed SQLite from ESP binary if not yet seeded. TOCTOU-safe: esp_exists() check AND bulk_in |
| `StringManager.mark_untranslatable` | `string_manager.py:237` | 24 | 4% | 1 | 🟠 не покрыт | Set translation=original, source='untranslatable', quality_score=100 for all strings where |
| `StringManager.reset_to_pending` | `string_manager.py:262` | 24 | 33% | 4 | 🟡 частично | Clear translations, set status='pending', source='pending'. Returns number of strings rese |
| `StringManager.approve_string` | `string_manager.py:287` | 22 | 36% | 8 | 🟡 частично | Set status='translated' for a needs_review string. Records history. |
| `StringMerger.__init__` | `string_merger.py:26` | 9 | 33% | 75 | 🟡 частично | Args: repo: StringRepo instance string_mgr: StringManager instance (optional; used for sav |
| `StringMerger.merge` | `string_merger.py:38` | 73 | 33% | 14 | 🟡 частично | Merge a fresh scan result against the existing DB rows. Args: mod_name: e.g. "SkyrimSE" es |
| `StringMerger._flag_changed` | `string_merger.py:114` | 40 | 18% | 1 | 🟡 частично | Set status='needs_review' and update original; write pre_rescan history. |
| `StringMerger._bulk_insert_new` | `string_merger.py:155` | 28 | 18% | 1 | 🟡 частично | Insert new (pending) strings in one transaction. |
| `StringMerger._soft_delete` | `string_merger.py:184` | 13 | 31% | 1 | 🟡 частично | Mark a key as deleted (it no longer exists in the ESP). |
| `_hash` | `translation_cache.py:16` | 3 | 67% | 8 | 🟢 покрыт | SHA256[:32] hex digest of text (16 bytes — negligible collision rate). |
| `TranslationCache.__init__` | `translation_cache.py:26` | 6 | 33% | 75 | 🟡 частично | Args: db: TranslationDB instance |
| `TranslationCache.lookup` | `translation_cache.py:33` | 16 | 6% | 15 | 🟠 не покрыт | Look up a translation for `original` by SHA256 hash. Returns the first matching translated |
| `TranslationCache.bulk_lookup` | `translation_cache.py:50` | 56 | 54% | 5 | 🟡 частично | Look up translations for a list of originals in a single query. Returns {original: transla |
| `TranslationCache.populate_hashes` | `translation_cache.py:107` | 51 | 39% | 3 | 🟡 частично | Compute real SHA256[:32] hashes for all rows with NULL string_hash. Throttled with a brief |
| `_localname` | `translation_import.py:26` | 2 | 100% | 2 | 🟢 покрыт |  |
| `parse_xtranslate_xml` | `translation_import.py:30` | 21 | 81% | 6 | 🟢 покрыт | Parse xTranslate/SST XML → list of (source, dest) pairs. Namespace-tolerant; ignores entri |
| `parse_string_pair` | `translation_import.py:53` | 14 | 79% | 2 | 🟢 покрыт | Join an English and a Russian string file by string id → (source, dest) pairs. |
| `import_pairs` | `translation_import.py:69` | 43 | 63% | 7 | 🟢 покрыт | Apply (source, dest) pairs to a mod's DB rows. Matches by original text (exact, then norma |
| `import_xtranslate_file` | `translation_import.py:114` | 2 | 50% | 0 | 🔴 мёртвый |  |

### Качество и валидация

| Функция / метод | Файл:строка | loc | Покрытие | Ссылок | Статус | Назначение |
|---|---|---|---|---|---|---|
| `extract_game_tokens` | `quality.py:28` | 3 | 67% | 16 | 🟢 покрыт | Extract inline game tokens from text (after stripping format tags). |
| `needs_translation` | `quality.py:33` | 24 | 79% | 50 | 🟢 покрыт |  |
| `validate_tokens` | `quality.py:59` | 13 | 54% | 17 | 🟡 частично | Check all game tokens from original appear in translation. Returns (ok: bool, issues: list |
| `quality_score` | `quality.py:74` | 53 | 64% | 245 | 🟢 покрыт | Heuristic quality score 0–100 for a translation. |
| `compute_string_status` | `quality.py:129` | 10 | 70% | 24 | 🟢 покрыт | Single source of truth: returns (quality_score, tok_ok, token_issues, status). status is ' |
| `_candidate_score` | `quality.py:141` | 7 | 71% | 4 | 🟢 покрыт | Comparable score for picking between two candidate translations: quality_score plus a bonu |
| `pick_better` | `quality.py:150` | 11 | 73% | 10 | 🟢 покрыт | Choose the better of two candidate translations (G6 — multi-agent quality). Lets a re-tran |
| `_contains_word` | `terminology.py:18` | 10 | 60% | 1 | 🟢 покрыт | Case-insensitive whole-word-ish containment (word boundaries, so 'Iron' doesn't match 'Iro |
| `terminology_report` | `terminology.py:30` | 27 | 52% | 6 | 🟡 частично | For each glossary term EN→RU, among translated strings whose original contains EN, count t |
| `terminology_summary` | `terminology.py:59` | 9 | 33% | 4 | 🟡 частично | Compact roll-up for the UI: how many glossary terms have inconsistencies and the total num |
| `Validator.validate` | `validator.py:21` | 8 | 0% | 24 | 🟠 не покрыт |  |

### Промпты и парсинг ответа

| Функция / метод | Файл:строка | loc | Покрытие | Ссылок | Статус | Назначение |
|---|---|---|---|---|---|---|
| `_preserve_note` | `builder.py:48` | 4 | 75% | 11 | 🟢 покрыт |  |
| `_numbered` | `builder.py:54` | 5 | 40% | 1 | 🟡 частично |  |
| `build_prompt` | `builder.py:61` | 38 | 21% | 25 | 🟡 частично | Assemble the full ChatML inference prompt. All dynamic data (terminology, preserve_tokens, |
| `build_raw_chatml` | `builder.py:101` | 8 | 12% | 2 | 🟡 частично | Build a generic ChatML prompt from explicit system + user strings. |
| `parse_numbered_output` | `parser.py:18` | 35 | 54% | 49 | 🟡 частично | Parse a numbered list from model output. Returns a list of length `expected`. If parsing f |
| `_multiline_parse` | `parser.py:55` | 17 | 41% | 3 | 🟡 частично | Fallback: split on numbered lines, capture multi-line values. |
| `_load_terms` | `builder.py:17` | 9 | 56% | 5 | 🟡 частично |  |
| `_terms_block` | `builder.py:30` | 8 | 50% | 1 | 🟡 частично | Legacy: fixed first-30 terms for local prompt templates. |
| `_terms_relevant` | `builder.py:40` | 27 | 56% | 13 | 🟡 частично | Return Skyrim terminology entries relevant to current_texts. Scores by word-overlap — only |
| `_preserve_note` | `builder.py:69` | 5 | 100% | 11 | 🟢 покрыт |  |
| `TranslationMemory.__init__` | `builder.py:98` | 4 | 100% | 75 | 🟢 покрыт |  |
| `TranslationMemory.add` | `builder.py:103` | 14 | 71% | 57 | 🟢 покрыт | Add a pair. No-op if too long, identical, or cap reached. |
| `TranslationMemory.build_block` | `builder.py:118` | 28 | 68% | 9 | 🟢 покрыт | Return a formatted TM block for prompt injection. Only includes entries that share words w |
| `TranslationMemory.__len__` | `builder.py:147` | 2 | 100% | 1 | 🟢 покрыт |  |
| `build_tm_block` | `builder.py:151` | 31 | 3% | 0 | 🔴 мёртвый | Stateless helper: build a TM block from a plain dict snapshot. Used by translate-one (sing |
| `enrich_context` | `builder.py:184` | 26 | 4% | 4 | 🟠 не покрыт | Build the full context string sent to the translation backend. Appends: 1. Relevant Skyrim |
| `build_prompt` | `builder.py:241` | 47 | 19% | 25 | 🟡 частично | Build the full inference prompt. Parameters ---------- system_prompt : override the defaul |
| `_build_qwen_prompt` | `builder.py:310` | 33 | 21% | 1 | 🟡 частично |  |
| `build_arbiter_prompt` | `builder.py:359` | 36 | 3% | 6 | 🟠 не покрыт |  |
| `parse_numbered_output` | `parser.py:18` | 35 | 57% | 49 | 🟡 частично | Parse a numbered list from model output. Returns a list of length `expected`. If parsing f |
| `_multiline_parse` | `parser.py:55` | 17 | 76% | 3 | 🟢 покрыт | Fallback: split on numbered lines, capture multi-line values. |

### Контекст

| Функция / метод | Файл:строка | loc | Покрытие | Ссылок | Статус | Назначение |
|---|---|---|---|---|---|---|
| `ContextBuilder.__init__` | `builder.py:29` | 5 | 20% | 75 | 🟡 частично |  |
| `ContextBuilder.get_mod_context` | `builder.py:37` | 23 | 4% | 21 | 🟠 не покрыт | Return a short description of the mod from Nexus (cached in memory + disk). force=True: by |
| `ContextBuilder._summary_cache_path` | `builder.py:61` | 3 | 33% | 2 | 🟡 частично |  |
| `ContextBuilder._load_summary_cache` | `builder.py:65` | 8 | 12% | 1 | 🟡 частично |  |
| `ContextBuilder._save_summary_cache` | `builder.py:74` | 8 | 12% | 1 | 🟡 частично |  |
| `ContextBuilder.get_esp_extractor` | `builder.py:85` | 4 | 25% | 1 | 🟡 частично |  |
| `ContextBuilder.get_record_context` | `builder.py:90` | 9 | 11% | 0 | 🔴 мёртвый |  |
| `ContextBuilder.build` | `builder.py:102` | 15 | 7% | 16 | 🟠 не покрыт | Assemble final context string for the prompt. |
| `RecordContext.__init__` | `esp_context.py:27` | 5 | 20% | 75 | 🟡 частично |  |
| `RecordContext.as_hint` | `esp_context.py:33` | 7 | 14% | 1 | 🟡 частично |  |
| `EspContextExtractor.__init__` | `esp_context.py:48` | 4 | 25% | 75 | 🟡 частично |  |
| `EspContextExtractor.get` | `esp_context.py:53` | 4 | 25% | 1491 | 🟡 частично |  |
| `EspContextExtractor.all_records` | `esp_context.py:58` | 4 | 25% | 1 | 🟡 частично |  |
| `EspContextExtractor._parse` | `esp_context.py:63` | 53 | 2% | 2 | 🟠 не покрыт |  |
| `EspContextExtractor._extract_edid` | `esp_context.py:118` | 24 | 4% | 1 | 🟠 не покрыт | Extract EDID subrecord value from a record's data section. |
| `_clean_markup` | `nexus_fetcher.py:33` | 12 | 8% | 1 | 🟠 не покрыт | Strip HTML and BBCode, return clean plain text. |
| `NexusFetcher.__init__` | `nexus_fetcher.py:53` | 11 | 9% | 75 | 🟠 не покрыт |  |
| `NexusFetcher._cache_get` | `nexus_fetcher.py:65` | 23 | 70% | 4 | 🟢 покрыт | Return (summary, age_days) from SQLite (preferred) or the legacy JSON file. |
| `NexusFetcher._cache_put` | `nexus_fetcher.py:89` | 22 | 50% | 3 | 🟡 частично | Write to SQLite (preferred) or the legacy JSON file. |
| `NexusFetcher.test_connection` | `nexus_fetcher.py:112` | 16 | 6% | 2 | 🟠 не покрыт | Ping the Nexus API to verify the API key works. |
| `NexusFetcher.fetch_mod_description` | `nexus_fetcher.py:129` | 9 | 11% | 4 | 🟠 не покрыт | Given a mod folder (MO2 mod directory), return the Nexus mod description. Returns None if  |
| `NexusFetcher._read_mod_id` | `nexus_fetcher.py:139` | 13 | 8% | 1 | 🟠 не покрыт |  |
| `NexusFetcher._get_description` | `nexus_fetcher.py:153` | 30 | 3% | 1 | 🟠 не покрыт |  |
| `NeuralSummarizer.__init__` | `summarizer.py:37` | 5 | 20% | 75 | 🟡 частично |  |
| `NeuralSummarizer.summarize` | `summarizer.py:43` | 14 | 7% | 6 | 🟠 не покрыт |  |
| `NeuralSummarizer._llm_summarize` | `summarizer.py:58` | 69 | 1% | 1 | 🟠 не покрыт | Use the LLM to generate a rich summary. Routes to the configured remote server when mode i |
| `_extractive_summarize` | `summarizer.py:129` | 26 | 4% | 1 | 🟠 не покрыт | Fallback: pick meaningful sentences, skip credits/noise. |

### Пайплайны перевода

| Функция / метод | Файл:строка | loc | Покрытие | Ссылок | Статус | Назначение |
|---|---|---|---|---|---|---|
| `_get_pipeline` | `pipeline.py:16` | 6 | 0% | 1 | 🟠 не покрыт |  |
| `translate_batch` | `pipeline.py:24` | 17 | 0% | 12 | 🟠 не покрыт | Translate a batch of strings using the ensemble pipeline. Returns list of same length. Nev |
| `get_mod_context` | `pipeline.py:43` | 12 | 0% | 21 | 🟠 не покрыт | Return a short description context string for a given mod folder. Returns "" if Nexus API  |
| `_apply_bsa_localized_strings` | `apply_pipeline.py:23` | 44 | 0% | 1 | 🟠 не покрыт | B — translate BSA-packed localized .STRINGS files. For each .bsa in the mod: unpack, trans |
| `ApplyPipeline.__init__` | `apply_pipeline.py:72` | 4 | 0% | 75 | 🟠 не покрыт |  |
| `ApplyPipeline.run_esp` | `apply_pipeline.py:79` | 76 | 0% | 1 | 🟠 не покрыт | Apply ESP translations from SQLite to ESP/ESM binaries. |
| `ApplyPipeline.run_bsa` | `apply_pipeline.py:158` | 102 | 0% | 1 | 🟠 не покрыт | Apply BSA/MCM/SWF translations from SQLite to disk and repack. |
| `ApplyPipeline._should_apply` | `apply_pipeline.py:263` | 30 | 0% | 2 | 🟠 не покрыт |  |
| `_translate_swf_texts` | `apply_pipeline.py:297` | 77 | 0% | 1 | 🟠 не покрыт | Extract text strings from SWF using FFDec, translate, reimport. |
| `RecomputePipeline.__init__` | `recompute_pipeline.py:14` | 3 | 0% | 75 | 🟠 не покрыт |  |
| `RecomputePipeline.run` | `recompute_pipeline.py:18` | 74 | 0% | 190 | 🟠 не покрыт |  |
| `TranslatePipeline.__init__` | `translate_pipeline.py:51` | 18 | 0% | 75 | 🟠 не покрыт |  |
| `TranslatePipeline.run` | `translate_pipeline.py:70` | 421 | 0% | 190 | 🟠 не покрыт | Run all 12 pipeline steps. Called from translate_strings_worker shim. |
| `TranslatePipeline._resolve_strings` | `translate_pipeline.py:494` | 111 | 0% | 1 | 🟠 не покрыт | Step 1: Load strings from SQLite and apply scope/mode filters. |
| `TranslatePipeline._apply_cache_hits` | `translate_pipeline.py:606` | 20 | 0% | 1 | 🟠 не покрыт | Step 5: Look up translations via TranslationCache. Save hits inline. |
| `TranslatePipeline._apply_dict_hits` | `translate_pipeline.py:627` | 24 | 0% | 1 | 🟠 не покрыт | Step 6: Look up translations via GlobalDict. Save hits inline. |
| `ValidatePipeline.__init__` | `validate_pipeline.py:26` | 4 | 0% | 75 | 🟠 не покрыт |  |
| `ValidatePipeline.run` | `validate_pipeline.py:31` | 85 | 0% | 190 | 🟠 не покрыт |  |

### Ансамбль моделей

| Функция / метод | Файл:строка | loc | Покрытие | Ссылок | Статус | Назначение |
|---|---|---|---|---|---|---|
| `resolve_consensus` | `consensus.py:23` | 55 | 0% | 0 | 🔴 мёртвый | For each (a, b) pair: - If jaccard(a, b) >= threshold → use a (Model A is faster) - Otherw |
| `EnsemblePipeline.__init__` | `pipeline.py:27` | 66 | 0% | 75 | 🟠 не покрыт |  |
| `EnsemblePipeline.translate` | `pipeline.py:94` | 33 | 0% | 205 | 🟠 не покрыт |  |
| `EnsemblePipeline._run` | `pipeline.py:130` | 45 | 0% | 36 | 🟠 не покрыт | Route each text to lite (14B) or full (32B) based on length. |
| `EnsemblePipeline._make_backend` | `pipeline.py:177` | 17 | 0% | 10 | 🟠 не покрыт | Instantiate the correct backend class based on ensemble.backend_type. |
| `EnsemblePipeline._backend_label` | `pipeline.py:196` | 6 | 0% | 8 | 🟠 не покрыт | Safe label for any backend type (LlamaCppBackend or RemoteBackend). |
| `EnsemblePipeline._translate_with` | `pipeline.py:203` | 16 | 0% | 3 | 🟠 не покрыт |  |
| `EnsemblePipeline._save_profile` | `pipeline.py:220` | 21 | 0% | 1 | 🟠 не покрыт | Append profiling data to cache/translation_profile.json. |
| `_cyrillic_tokens` | `similarity.py:7` | 3 | 0% | 2 | 🟠 не покрыт | Extract only Cyrillic characters (lowercased) from text. |
| `_char_bigrams` | `similarity.py:12` | 4 | 0% | 2 | 🟠 не покрыт |  |
| `jaccard_similarity` | `similarity.py:18` | 22 | 0% | 4 | 🟠 не покрыт | Jaccard similarity over char-bigrams of Cyrillic content. Returns 0.0 if both strings have |

### Модели (host)

| Функция / метод | Файл:строка | loc | Покрытие | Ссылок | Статус | Назначение |
|---|---|---|---|---|---|---|
| `BaseBackend.__init__` | `base.py:27` | 2 | 50% | 75 | 🟡 частично |  |
| `BaseBackend.is_loaded` | `base.py:31` | 2 | 50% | 28 | 🟡 частично |  |
| `BaseBackend.load` | `base.py:35` | 2 | 50% | 166 | 🟡 частично | Load model weights into VRAM. |
| `BaseBackend.translate` | `base.py:39` | 10 | 10% | 205 | 🟠 не покрыт | Translate a list of strings. Returns a list of the same length. Never raises — returns ori |
| `BaseBackend.unload` | `base.py:50` | 10 | 10% | 31 | 🟠 не покрыт | Free GPU memory. Call between model swaps. |
| `BaseBackend._do_unload` | `base.py:61` | 2 | 50% | 8 | 🟡 частично | Override in subclasses to release model references. |
| `BaseBackend.__enter__` | `base.py:64` | 3 | 33% | 1 | 🟡 частично |  |
| `BaseBackend.__exit__` | `base.py:68` | 2 | 50% | 1 | 🟡 частично |  |
| `InferenceParams.as_dict` | `inference_params.py:36` | 12 | 17% | 14 | 🟡 частично | Serialise for HTTP transport (remote backend → server). |
| `InferenceParams.from_dict` | `inference_params.py:50` | 12 | 17% | 22 | 🟡 частично | Deserialise from HTTP request dict (server side). |
| `InferenceParams.defaults` | `inference_params.py:64` | 3 | 67% | 34 | 🟢 покрыт | Return params with all None — backend will use its ModelConfig defaults. |
| `get_token_stats` | `llamacpp_backend.py:35` | 2 | 50% | 7 | 🟡 частично |  |
| `get_performance_stats` | `llamacpp_backend.py:39` | 13 | 8% | 7 | 🟠 не покрыт |  |
| `reset_token_stats` | `llamacpp_backend.py:54` | 7 | 14% | 3 | 🟡 частично |  |
| `LlamaCppBackend.__init__` | `llamacpp_backend.py:69` | 10 | 10% | 75 | 🟠 не покрыт |  |
| `LlamaCppBackend.load` | `llamacpp_backend.py:80` | 27 | 4% | 166 | 🟠 не покрыт |  |
| `LlamaCppBackend._do_unload` | `llamacpp_backend.py:108` | 3 | 33% | 8 | 🟡 частично |  |
| `LlamaCppBackend.translate` | `llamacpp_backend.py:112` | 23 | 4% | 205 | 🟠 не покрыт |  |
| `LlamaCppBackend.arbitrate` | `llamacpp_backend.py:136` | 29 | 3% | 2 | 🟠 не покрыт | Pick the best translation given two candidates. |
| `LlamaCppBackend._chat` | `llamacpp_backend.py:168` | 38 | 3% | 11 | 🟠 не покрыт |  |
| `LlamaCppBackend._translate_batch` | `llamacpp_backend.py:207` | 14 | 7% | 4 | 🟠 не покрыт |  |
| `LlamaCppBackend._arbitrate_batch` | `llamacpp_backend.py:222` | 33 | 3% | 1 | 🟠 не покрыт |  |
| `default_model_cache_dir` | `loader.py:16` | 9 | 11% | 1 | 🟠 не покрыт | Return the default model cache directory: <project_root>/models/ Used when no config.yaml  |
| `_has_model_files` | `loader.py:27` | 5 | 20% | 2 | 🟡 частично | Check directory has at least a config.json or .safetensors shard. |
| `_get_model_cache_dir` | `loader.py:34` | 8 | 12% | 2 | 🟡 частично | Return model_cache_dir from config if loaded, else platform default. |
| `resolve` | `loader.py:44` | 39 | 3% | 19 | 🟠 не покрыт | Return a path string suitable for from_pretrained(). Checks local model_cache_dir first, t |
| `resolve_gguf` | `loader.py:85` | 55 | 2% | 7 | 🟠 не покрыт | Return absolute path to a .gguf file (first shard if split). Resolution order: 1. If local |
| `load_causal_lm` | `loader.py:142` | 33 | 3% | 0 | 🔴 мёртвый | Load a causal LM + tokenizer, handling GPTQ and CPU offload. Returns (tokenizer, model). |
| `_find_cached_snapshot` | `mlx_backend.py:18` | 23 | 0% | 6 | 🟠 не покрыт | Scan cache_dir for an existing MLX model snapshot — no network access. |
| `MlxBackend.__init__` | `mlx_backend.py:55` | 29 | 0% | 75 | 🟠 не покрыт |  |
| `MlxBackend.load` | `mlx_backend.py:87` | 54 | 0% | 166 | 🟠 не покрыт |  |
| `MlxBackend._do_unload` | `mlx_backend.py:142` | 10 | 0% | 8 | 🟠 не покрыт | Delete model references and clear MLX cache. |
| `MlxBackend.translate` | `mlx_backend.py:153` | 75 | 0% | 205 | 🟠 не покрыт | Translate strings using mlx_lm.generate(). Returns originals on any error. Never raises. p |
| `MlxBackend._infer` | `mlx_backend.py:229` | 32 | 0% | 14 | 🟠 не покрыт | Raw inference from a pre-built prompt string. Called by the server's /infer endpoint — no  |
| `MlxBackend._chat` | `mlx_backend.py:262` | 33 | 0% | 11 | 🟠 не покрыт | Raw chat inference — no translation prompt wrapping. Used by the server's /chat endpoint. |
| `get_remote_token_stats` | `remote_backend.py:24` | 2 | 50% | 2 | 🟡 частично |  |
| `reset_remote_token_stats` | `remote_backend.py:28` | 3 | 33% | 2 | 🟡 частично |  |
| `RemoteBackend.__init__` | `remote_backend.py:50` | 15 | 7% | 75 | 🟠 не покрыт |  |
| `RemoteBackend.load` | `remote_backend.py:68` | 16 | 6% | 166 | 🟠 не покрыт | Verify connectivity and fetch server info. No local model to load. |
| `RemoteBackend._do_unload` | `remote_backend.py:85` | 4 | 25% | 8 | 🟡 частично | Close HTTP session. No GPU memory to free. |
| `RemoteBackend.translate` | `remote_backend.py:90` | 94 | 1% | 205 | 🟠 не покрыт | Translate via remote server. Builds the full ChatML prompt on the Windows (client) side us |
| `RemoteBackend.server_info` | `remote_backend.py:186` | 2 | 50% | 0 | 🟡 частично |  |

### Парсинг форматов

| Функция / метод | Файл:строка | loc | Покрытие | Ссылок | Статус | Назначение |
|---|---|---|---|---|---|---|
| `apply_mcm_from_db` | `asset_extractor.py:20` | 56 | 0% | 6 | 🟠 не покрыт | Generate *_russian.txt files for loose MCM strings from SQLite. Reads all mcm: rows for th |
| `apply_bsa_mcm_from_db` | `asset_extractor.py:78` | 57 | 0% | 3 | 🟠 не покрыт | Generate *_russian.txt files for BSA-embedded MCM strings from SQLite. Returns number of f |
| `apply_swf_from_db` | `asset_extractor.py:137` | 30 | 0% | 3 | 🟠 не покрыт | Generate {chid}_ru.txt files in the SWF cache from SQLite. Returns number of files written |
| `apply_all_assets` | `asset_extractor.py:169` | 16 | 0% | 3 | 🟠 не покрыт | Apply MCM + BSA-MCM + SWF translations from DB to disk files. Returns (mcm_written, bsa_mc |
| `unpack` | `bsa_handler.py:13` | 11 | 0% | 26 | 🟠 не покрыт | Unpack a BSA archive using BSArch. Raises RuntimeError on non-zero exit. |
| `pack` | `bsa_handler.py:26` | 11 | 0% | 43 | 🟠 не покрыт | Pack a directory into a BSA archive using BSArch (SSE format). Raises RuntimeError on non- |
| `extract_strings` | `esp_parser.py:12` | 8 | 0% | 3 | 🟠 не покрыт | Extract all translatable strings from an ESP/ESM binary. Returns (strings, header) where s |
| `rewrite` | `esp_parser.py:22` | 21 | 0% | 13 | 🟠 не покрыт | Write translations back into an ESP binary. translations: list of dicts as returned by Str |
| `read` | `mcm_handler.py:14` | 9 | 0% | 50 | 🟠 не покрыт | Read an MCM translation .txt file. Returns (pairs, bom) where: pairs — list of (key, value |
| `write` | `mcm_handler.py:25` | 13 | 0% | 52 | 🟠 не покрыт | Write MCM translation pairs to a UTF-16-LE .txt file. Args: path: destination path pairs:  |
| `export_texts` | `swf_handler.py:15` | 16 | 0% | 3 | 🟠 не покрыт | Export text strings from a SWF file to a directory. Raises RuntimeError on non-zero exit. |
| `import_texts` | `swf_handler.py:33` | 18 | 0% | 3 | 🟠 не покрыт | Import translated text files back into a SWF. Raises RuntimeError on non-zero exit. |
| `decompile` | `swf_handler.py:53` | 16 | 0% | 7 | 🟠 не покрыт | Full decompile (all assets) of a SWF. Raises RuntimeError on non-zero exit. |
| `compile_texts` | `swf_handler.py:71` | 18 | 0% | 4 | 🟠 не покрыт | Recompile a SWF from a decompiled scripts directory. Raises RuntimeError on non-zero exit. |
| `list_fonts` | `swf_handler.py:91` | 31 | 0% | 2 | 🟠 не покрыт | Return a list of fonts embedded in a SWF. Each dict: {id: int, name: str, style: str} Rais |
| `replace_font` | `swf_handler.py:124` | 19 | 0% | 2 | 🟠 не покрыт | Replace a font in a SWF by its ID with a TTF file. Raises RuntimeError on non-zero exit. |
| `replace_font_by_name` | `swf_handler.py:145` | 19 | 0% | 2 | 🟠 не покрыт | Replace a font in a SWF by its name with a TTF file. Raises RuntimeError on non-zero exit. |

### Движки файлов (scripts)

| Функция / метод | Файл:строка | loc | Покрытие | Ссылок | Статус | Назначение |
|---|---|---|---|---|---|---|
| `_get_cfg` | `esp_engine.py:25` | 3 | 100% | 4 | 🟢 покрыт |  |
| `_paths` | `esp_engine.py:30` | 3 | 100% | 8 | 🟢 покрыт |  |
| `is_translatable` | `esp_engine.py:64` | 6 | 83% | 2 | 🟢 покрыт |  |
| `u32` | `esp_engine.py:74` | 1 | 100% | 12 | 🟢 покрыт |  |
| `u16` | `esp_engine.py:75` | 1 | 100% | 7 | 🟢 покрыт |  |
| `p32` | `esp_engine.py:76` | 1 | 100% | 6 | 🟢 покрыт |  |
| `p16` | `esp_engine.py:77` | 1 | 100% | 4 | 🟢 покрыт |  |
| `set_string_encoding` | `esp_engine.py:86` | 5 | 40% | 4 | 🟡 частично | Override the embedded-string output encoding (e.g. 'cp1251' for RU installs that render UT |
| `read_cstring` | `esp_engine.py:92` | 9 | 89% | 4 | 🟢 покрыт |  |
| `write_cstring` | `esp_engine.py:102` | 2 | 100% | 4 | 🟢 покрыт |  |
| `parse_subrecords` | `esp_engine.py:108` | 25 | 40% | 2 | 🟡 частично | Yield (field_type:bytes, field_data:bytes) for each subrecord. Handles XXXX large-size pre |
| `build_subrecords` | `esp_engine.py:135` | 10 | 70% | 1 | 🟢 покрыт |  |
| `Record.__init__` | `esp_engine.py:153` | 8 | 100% | 75 | 🟢 покрыт |  |
| `Record.compressed` | `esp_engine.py:163` | 2 | 100% | 5 | 🟢 покрыт |  |
| `Record.decompressed_data` | `esp_engine.py:166` | 5 | 60% | 2 | 🟢 покрыт |  |
| `Record.recompress` | `esp_engine.py:172` | 3 | 33% | 1 | 🟡 частично |  |
| `Record.to_bytes` | `esp_engine.py:176` | 5 | 60% | 1 | 🟢 покрыт |  |
| `iter_esp` | `esp_engine.py:183` | 21 | 86% | 4 | 🟢 покрыт |  |
| `parse_vmad_strings` | `esp_engine.py:208` | 72 | 54% | 4 | 🟡 частично | Parse VMAD subrecord, return list of (len_prefix_offset, old_length, text). Extracts prope |
| `rewrite_vmad_strings` | `esp_engine.py:282` | 29 | 41% | 3 | 🟡 частично | Rebuild VMAD bytes with translated strings. translations: {vmad_str_idx: new_text} Returns |
| `extract_strings_from_record` | `esp_engine.py:315` | 90 | 40% | 1 | 🟡 частично |  |
| `extract_all_strings` | `esp_engine.py:407` | 30 | 67% | 33 | 🟢 покрыт |  |
| `apply_translations_to_record` | `esp_engine.py:441` | 50 | 58% | 1 | 🟡 частично |  |
| `rewrite_esp` | `esp_engine.py:493` | 26 | 88% | 11 | 🟢 покрыт |  |
| `prepare_for_ai` | `esp_engine.py:539` | 22 | 54% | 12 | 🟡 частично | Prepare texts for AI translation: 1. Replace HTML formatting tags (font/p/br/img/b/i/…) wi |
| `strip_echo` | `esp_engine.py:563` | 14 | 21% | 5 | 🟡 частично | Remove 'source → translation' echo that some models output. Keeps only the right-hand side |
| `restore_from_ai` | `esp_engine.py:579` | 21 | 52% | 11 | 🟡 частично | Reverse prepare_for_ai: restore {T0}/{T1}/… and ⟨H0⟩/⟨H1⟩/… tokens. |
| `extract_game_tokens` | `esp_engine.py:602` | 3 | 33% | 16 | 🟡 частично | Extract inline game tokens from text (after stripping format tags). |
| `validate_tokens` | `esp_engine.py:607` | 14 | 7% | 17 | 🟠 не покрыт | Check all game tokens from original appear in translation. Returns (ok: bool, issues: list |
| `compute_string_status` | `esp_engine.py:623` | 10 | 10% | 24 | 🟠 не покрыт | Single source of truth: returns (quality_score, tok_ok, token_issues, status). status is ' |
| `translate_batch` | `esp_engine.py:637` | 9 | 11% | 12 | 🟠 не покрыт | Delegate to translator.pipeline (ensemble). |
| `translate_texts` | `esp_engine.py:648` | 47 | 2% | 6 | 🟠 не покрыт | Core translation pipeline shared by single-string and batch flows. Steps: needs_translatio |
| `needs_translation` | `esp_engine.py:697` | 26 | 4% | 50 | 🟠 не покрыт |  |
| `quality_score` | `esp_engine.py:725` | 48 | 2% | 245 | 🟠 не покрыт | Heuristic quality score 0–100 for a translation. Used to flag potentially bad translations |
| `translate_strings` | `esp_engine.py:775` | 77 | 1% | 28 | 🟠 не покрыт | Translate all strings in one pipeline call (model loads once, not per batch). progress_cb( |
| `cmd_inspect` | `esp_engine.py:856` | 11 | 9% | 1 | 🟠 не покрыт |  |
| `cmd_export` | `esp_engine.py:869` | 4 | 25% | 2 | 🟡 частично |  |
| `_update_caches` | `esp_engine.py:875` | 38 | 60% | 5 | 🟢 покрыт | Update translation_cache.json (for scanner translated count) and _string_counts.json (for  |
| `_build_trans_map` | `esp_engine.py:915` | 13 | 8% | 4 | 🟠 не покрыт | Build trans_map dict from a strings list (for rewrite_esp). |
| `_backup_esp` | `esp_engine.py:930` | 17 | 59% | 5 | 🟡 частично | Create a backup of esp_path in the backup_dir if not already backed up. |
| `cmd_translate` | `esp_engine.py:949` | 54 | 2% | 11 | 🟠 не покрыт | Translate an ESP. apply_esp=True → full pipeline: AI translate + rewrite ESP binary (defau |
| `cmd_apply_from_trans` | `esp_engine.py:1005` | 21 | 5% | 0 | 🔴 мёртвый | Apply translations from .trans.json to ESP binary (no AI translation). Used for the separa |
| `_is_localized` | `esp_engine.py:1028` | 5 | 60% | 1 | 🟢 покрыт |  |
| `apply_localized_strings` | `esp_engine.py:1035` | 63 | 48% | 4 | 🟡 частично | Apply translations for a *localized* plugin by rewriting its sibling .STRINGS/ .ILSTRINGS/ |
| `cmd_apply_from_strings` | `esp_engine.py:1100` | 31 | 23% | 10 | 🟡 частично | Apply translations from a list of string dicts — no .trans.json file needed. strings: list |
| `cmd_apply` | `esp_engine.py:1133` | 7 | 14% | 1 | 🟡 частично |  |
| `cmd_run` | `esp_engine.py:1142` | 7 | 14% | 1 | 🟡 частично |  |
| `main` | `esp_engine.py:1153` | 41 | 2% | 229 | 🟠 не покрыт |  |
| `_looks_like_text` | `pex_engine.py:28` | 11 | 54% | 2 | 🟡 частично | Heuristic: display text vs identifier. Require a space and a lowercase letter, which exclu |
| `_read_lenstr` | `pex_engine.py:41` | 4 | 100% | 2 | 🟢 покрыт |  |
| `_table_offset` | `pex_engine.py:47` | 9 | 78% | 1 | 🟢 покрыт | Byte offset where the string table (count u16) begins. |
| `parse_string_table` | `pex_engine.py:58` | 10 | 90% | 6 | 🟢 покрыт | Return (strings, table_start, table_end). |
| `extract_display_strings` | `pex_engine.py:70` | 6 | 17% | 1 | 🟡 частично | Read-only: candidate translatable display strings from a .pex. Returns [{index, text}] for |
| `_build_table` | `pex_engine.py:78` | 6 | 100% | 1 | 🟢 покрыт |  |
| `rewrite_pex_strings` | `pex_engine.py:86` | 26 | 65% | 3 | 🟢 покрыт | Replace string-table entries by index, keeping count + order so all references stay valid. |
| `kind_for` | `strings_codec.py:59` | 3 | 67% | 5 | 🟢 покрыт | Which string-file kind a localized (rec_type, field_type) resolves to. |
| `_decode` | `strings_codec.py:64` | 7 | 86% | 1 | 🟢 покрыт |  |
| `parse_strings_bytes` | `strings_codec.py:73` | 25 | 84% | 14 | 🟢 покрыт | Parse a .STRINGS/.ILSTRINGS/.DLSTRINGS blob → {string_id: text}. |
| `build_strings_bytes` | `strings_codec.py:100` | 24 | 75% | 17 | 🟢 покрыт | Serialize {string_id: text} → a valid .STRINGS/.ILSTRINGS/.DLSTRINGS blob. Directory entri |
| `strings_dir_paths` | `strings_codec.py:128` | 7 | 57% | 3 | 🟡 частично | Resolve the three sibling string files for a plugin. By default they live in `<plugin dir> |
| `discover_language` | `strings_codec.py:137` | 15 | 73% | 1 | 🟢 покрыт | Find which language the plugin's string files are actually in by globbing `<PluginStem>_*. |
| `extract_strings_dir` | `strings_codec.py:154` | 17 | 65% | 3 | 🟢 покрыт | Extract every localized string from the .STRINGS/.ILSTRINGS/.DLSTRINGS files directly unde |
| `translate_strings_dir` | `strings_codec.py:173` | 25 | 76% | 5 | 🟢 покрыт | Apply translations to every localized string file under `strings_dir` (unpacked BSA), matc |
| `LocalizedStrings.__init__` | `strings_codec.py:204` | 6 | 100% | 75 | 🟢 покрыт |  |
| `LocalizedStrings.load` | `strings_codec.py:212` | 14 | 93% | 166 | 🟢 покрыт |  |
| `LocalizedStrings.available` | `strings_codec.py:228` | 2 | 100% | 55 | 🟢 покрыт |  |
| `LocalizedStrings.text` | `strings_codec.py:231` | 3 | 100% | 2785 | 🟢 покрыт |  |
| `LocalizedStrings.merged` | `strings_codec.py:235` | 5 | 100% | 16 | 🟢 покрыт |  |
| `LocalizedStrings.set` | `strings_codec.py:241` | 8 | 75% | 163 | 🟢 покрыт | Update the text for an id in whichever file it belongs to. Returns False if the id isn't k |
| `LocalizedStrings.write` | `strings_codec.py:250` | 13 | 92% | 52 | 🟢 покрыт | Re-emit every string file that has content. Returns the paths written. |
| `_get_cfg` | `translate_mcm.py:28` | 3 | 0% | 4 | 🟠 не покрыт |  |
| `_paths` | `translate_mcm.py:33` | 2 | 0% | 8 | 🟠 не покрыт |  |
| `read_trans_file` | `translate_mcm.py:39` | 21 | 0% | 20 | 🟠 не покрыт | Return ([(key, value), ...], bom_bytes). Handles UTF-16 LE/BE and UTF-8. |
| `backup_if_exists` | `translate_mcm.py:62` | 10 | 0% | 1 | 🟠 не покрыт |  |
| `write_trans_file` | `translate_mcm.py:74` | 8 | 0% | 2 | 🟠 не покрыт |  |
| `needs_translation` | `translate_mcm.py:86` | 6 | 0% | 50 | 🟠 не покрыт |  |
| `translate_batch` | `translate_mcm.py:94` | 9 | 0% | 12 | 🟠 не покрыт |  |
| `get_from_bsa` | `translate_mcm.py:110` | 25 | 0% | 1 | 🟠 не покрыт |  |
| `repack_bsa` | `translate_mcm.py:137` | 38 | 0% | 4 | 🟠 не покрыт | Repack a modified BSA (call after translating files extracted from it). |
| `translate_one` | `translate_mcm.py:179` | 56 | 0% | 9 | 🟠 не покрыт |  |
| `cmd_translate_mcm` | `translate_mcm.py:239` | 67 | 0% | 12 | 🟠 не покрыт | Scan mod_folder for MCM translation files and translate them. Handles both loose files and |
| `load_progress` | `translate_mcm.py:310` | 7 | 0% | 2 | 🟠 не покрыт |  |
| `save_progress` | `translate_mcm.py:319` | 5 | 0% | 1 | 🟠 не покрыт |  |
| `load_items` | `translate_mcm.py:326` | 20 | 0% | 2 | 🟠 не покрыт |  |
| `main` | `translate_mcm.py:350` | 95 | 0% | 229 | 🟠 не покрыт |  |

### Джобы и оркестрация

| Функция / метод | Файл:строка | loc | Покрытие | Ссылок | Статус | Назначение |
|---|---|---|---|---|---|---|
| `AssignmentManager.__init__` | `assignment_manager.py:43` | 2 | 100% | 75 | 🟢 покрыт |  |
| `AssignmentManager.transition` | `assignment_manager.py:48` | 16 | 69% | 175 | 🟢 покрыт | Validate and apply a state transition. Returns False (and does nothing) if the transition  |
| `AssignmentManager.settle_delivery` | `assignment_manager.py:65` | 11 | 82% | 6 | 🟢 покрыт | Move an assignment toward a terminal state based on delivery counts. Returns the resulting |
| `AssignmentManager.recover_on_boot` | `assignment_manager.py:79` | 18 | 61% | 2 | 🟢 покрыт | Scan non-terminal assignments at startup. We do NOT drop them — they are durable. They sta |
| `AssignmentManager.job_progress` | `assignment_manager.py:100` | 9 | 33% | 4 | 🟡 частично | (total, delivered) summed across all assignments of a job — the source of truth for a job' |
| `AssignmentManager.is_job_done` | `assignment_manager.py:110` | 6 | 67% | 3 | 🟢 покрыт | True when every assignment of a job is terminal (complete/failed/orphaned). |
| `AssignmentManager.liveness_tier` | `assignment_manager.py:119` | 13 | 54% | 5 | 🟡 частично | Classify an assignment's agent: connected — lease still valid (recent heartbeat) disconnec |
| `AssignmentManager.reap` | `assignment_manager.py:133` | 16 | 62% | 4 | 🟢 покрыт | Orphan assignments whose agent is presumed dead. Conservative: only touches agents silent  |
| `AssignmentManager.abandon_agent` | `assignment_manager.py:150` | 9 | 78% | 3 | 🟢 покрыт | Operator action: immediately orphan all of an agent's active assignments (e.g. you know a  |
| `AssignmentManager.reassignable_string_ids` | `assignment_manager.py:160` | 7 | 71% | 5 | 🟢 покрыт | Undelivered string ids across all orphaned assignments — the work that a fresh dispatch sh |
| `verify_result_hash` | `assignment_store.py:35` | 7 | 57% | 9 | 🟡 частично | Self-consistency integrity check: does the agent's claimed string_hash actually match the  |
| `AssignmentStore.__init__` | `assignment_store.py:45` | 2 | 100% | 75 | 🟢 покрыт |  |
| `AssignmentStore.get_agent_cursor` | `assignment_store.py:50` | 5 | 60% | 6 | 🟢 покрыт |  |
| `AssignmentStore.reset_agent_cursors` | `assignment_store.py:56` | 15 | 47% | 1 | 🟡 частично | Reset pull cursors to 0 so the master re-pulls everything from agents. Used after restorin |
| `AssignmentStore.advance_agent_cursor` | `assignment_store.py:72` | 14 | 36% | 7 | 🟡 частично | Monotonic advance — never moves the cursor backwards. |
| `AssignmentStore.create_assignment` | `assignment_store.py:89` | 28 | 21% | 29 | 🟡 частично | Persist an assignment and its manifest atomically. |
| `AssignmentStore.get_assignment` | `assignment_store.py:118` | 5 | 60% | 19 | 🟢 покрыт |  |
| `AssignmentStore.list_assignments` | `assignment_store.py:124` | 14 | 79% | 5 | 🟢 покрыт |  |
| `AssignmentStore.list_active` | `assignment_store.py:139` | 6 | 50% | 5 | 🟡 частично |  |
| `AssignmentStore.set_state` | `assignment_store.py:146` | 8 | 50% | 5 | 🟡 частично |  |
| `AssignmentStore.touch_lease` | `assignment_store.py:155` | 12 | 42% | 1 | 🟡 частично | Refresh the lease on an agent's active assignments (called on heartbeat). |
| `AssignmentStore.mark_string_delivered` | `assignment_store.py:170` | 14 | 43% | 14 | 🟡 частично |  |
| `AssignmentStore.undelivered_string_ids` | `assignment_store.py:185` | 6 | 33% | 4 | 🟡 частично |  |
| `AssignmentStore.counts` | `assignment_store.py:192` | 7 | 43% | 71 | 🟡 частично | (total, delivered) for an assignment. |
| `AssignmentStore.diff_handshake` | `assignment_store.py:200` | 24 | 50% | 6 | 🟡 частично | Compare an agent's reported open assignments (from its ResultStore digest) against host st |
| `AssignmentStore.expected_hash` | `assignment_store.py:225` | 8 | 12% | 0 | 🔴 мёртвый | The hash the master expects for a string — for cross-checking deliveries against what was  |
| `JobCenter.get` | `job_center.py:38` | 4 | 100% | 1491 | 🟢 покрыт |  |
| `JobCenter.__init__` | `job_center.py:43` | 11 | 46% | 75 | 🟡 частично |  |
| `JobCenter.hub` | `job_center.py:58` | 2 | 50% | 73 | 🟡 частично |  |
| `JobCenter.submit` | `job_center.py:61` | 9 | 11% | 11 | 🟠 не покрыт | Submit a job to the appropriate pool. Returns the job immediately. |
| `JobCenter._route_pool` | `job_center.py:73` | 6 | 17% | 1 | 🟡 частично |  |
| `JobCenter._run` | `job_center.py:80` | 22 | 82% | 36 | 🟢 покрыт | Execute fn(job) on a pool thread; handle status transitions. |
| `NotificationHub.__init__` | `notification_hub.py:28` | 4 | 100% | 75 | 🟢 покрыт |  |
| `NotificationHub.subscribe` | `notification_hub.py:35` | 5 | 20% | 14 | 🟡 частично |  |
| `NotificationHub.unsubscribe` | `notification_hub.py:41` | 5 | 20% | 8 | 🟡 частично |  |
| `NotificationHub.subscribe_all` | `notification_hub.py:47` | 2 | 50% | 4 | 🟡 частично |  |
| `NotificationHub.unsubscribe_all` | `notification_hub.py:50` | 2 | 50% | 4 | 🟡 частично |  |
| `NotificationHub.publish` | `notification_hub.py:55` | 14 | 7% | 7 | 🟠 не покрыт | Serialise payload to JSON and deliver to all subscribers for job_id and to the global "__a |
| `NotificationHub._put` | `notification_hub.py:70` | 10 | 10% | 2 | 🟠 не покрыт |  |
| `content_hash` | `work_ledger.py:63` | 3 | 67% | 28 | 🟢 покрыт | Stable hash of the source text → cross-mod dedup key (identical English → reuse). |
| `WorkLedger.__init__` | `work_ledger.py:71` | 3 | 100% | 75 | 🟢 покрыт |  |
| `WorkLedger.append` | `work_ledger.py:76` | 13 | 62% | 276 | 🟢 покрыт |  |
| `WorkLedger.queue` | `work_ledger.py:91` | 2 | 100% | 113 | 🟢 покрыт |  |
| `WorkLedger.assign` | `work_ledger.py:94` | 2 | 100% | 21 | 🟢 покрыт |  |
| `WorkLedger.start` | `work_ledger.py:97` | 2 | 100% | 80 | 🟢 покрыт |  |
| `WorkLedger.result` | `work_ledger.py:100` | 3 | 67% | 525 | 🟢 покрыт |  |
| `WorkLedger.commit` | `work_ledger.py:104` | 2 | 100% | 119 | 🟢 покрыт |  |
| `WorkLedger.fail` | `work_ledger.py:107` | 3 | 67% | 4 | 🟢 покрыт |  |
| `WorkLedger.release` | `work_ledger.py:111` | 2 | 100% | 9 | 🟢 покрыт |  |
| `WorkLedger._events` | `work_ledger.py:115` | 4 | 50% | 3 | 🟡 частично |  |
| `WorkLedger.state` | `work_ledger.py:120` | 6 | 83% | 526 | 🟢 покрыт | Current derived state of a work item, or None if it has no events. |
| `WorkLedger.owner` | `work_ledger.py:127` | 9 | 89% | 10 | 🟢 покрыт | Agent that currently owns the item (last assign/start), or None if open/done. |
| `WorkLedger.translation` | `work_ledger.py:137` | 10 | 70% | 756 | 🟢 покрыт | Most recent translation from a RESULT event, if any. |
| `WorkLedger.is_done` | `work_ledger.py:148` | 2 | 100% | 3 | 🟢 покрыт |  |
| `WorkLedger.open_keys` | `work_ledger.py:151` | 11 | 64% | 2 | 🟢 покрыт | Work keys still needing an agent (queued or failed, not owned/done). This is what a dispat |
| `WorkLedger.dedup_translation` | `work_ledger.py:163` | 15 | 60% | 4 | 🟢 покрыт | Cross-mod reuse: any done translation for this source-text hash. Matches the hash on ANY e |
| `WorkLedger.progress` | `work_ledger.py:179` | 12 | 75% | 162 | 🟢 покрыт | Funnel for a job: counts per derived state. Replaces the assignment tally. |
| `WorkLedger.global_stats` | `work_ledger.py:192` | 26 | 35% | 2 | 🟡 частично | Fleet-wide projection folded from the log: total events, distinct done work items, unique  |
| `WorkLedger.recover_open` | `work_ledger.py:219` | 18 | 67% | 2 | 🟢 покрыт | After an agent dies: every key it owned (assigned/in_flight) that never reached a result i |
| `post_job_hook` | `job_hooks.py:13` | 40 | 2% | 11 | 🟠 не покрыт | Invalidate scanner cache and recompute materialized stats. Args: scanner: ModScanner insta |
| `Job.__post_init__` | `job_manager.py:57` | 5 | 100% | 0 | 🟢 покрыт |  |
| `Job.to_dict` | `job_manager.py:63` | 32 | 6% | 36 | 🟠 не покрыт |  |
| `Job._elapsed` | `job_manager.py:96` | 5 | 100% | 8 | 🟢 покрыт |  |
| `Job._eta_seconds` | `job_manager.py:102` | 11 | 82% | 4 | 🟢 покрыт |  |
| `Job.add_log` | `job_manager.py:114` | 10 | 70% | 148 | 🟢 покрыт |  |
| `JobManager.get` | `job_manager.py:137` | 4 | 100% | 1491 | 🟢 покрыт |  |
| `JobManager.__init__` | `job_manager.py:142` | 12 | 67% | 75 | 🟢 покрыт |  |
| `JobManager.set_app` | `job_manager.py:155` | 3 | 67% | 1 | 🟢 покрыт | Register the Flask app so job functions run inside an app context. |
| `JobManager.set_persist_path` | `job_manager.py:159` | 3 | 100% | 1 | 🟢 покрыт |  |
| `JobManager.create` | `job_manager.py:165` | 39 | 62% | 64 | 🟢 покрыт |  |
| `JobManager.begin_inline_job` | `job_manager.py:205` | 38 | 3% | 1 | 🟠 не покрыт | Create a RUNNING job for a synchronous (inline) operation. The caller must call finish_inl |
| `JobManager.update_inline_job` | `job_manager.py:244` | 41 | 2% | 4 | 🟠 не покрыт | Push an intermediate SSE update for a running inline job. Feeding tokens_done/tokens_total |
| `JobManager.finish_inline_job` | `job_manager.py:286` | 35 | 3% | 2 | 🟠 не покрыт | Mark an inline job as DONE or FAILED and broadcast the final state. |
| `JobManager.record_completed_job` | `job_manager.py:322` | 52 | 2% | 2 | 🟠 не покрыт | Create an already-completed job record (no queue, instant DONE/FAILED). Used for synchrono |
| `JobManager.get_job` | `job_manager.py:375` | 2 | 100% | 23 | 🟢 покрыт |  |
| `JobManager.list_jobs` | `job_manager.py:378` | 5 | 20% | 6 | 🟡 частично |  |
| `JobManager.cancel` | `job_manager.py:384` | 8 | 88% | 39 | 🟢 покрыт |  |
| `JobManager.clear_finished` | `job_manager.py:393` | 7 | 86% | 4 | 🟢 покрыт |  |
| `JobManager.subscribe` | `job_manager.py:403` | 2 | 50% | 14 | 🟡 частично |  |
| `JobManager.unsubscribe` | `job_manager.py:406` | 2 | 50% | 8 | 🟡 частично |  |
| `JobManager.subscribe_all` | `job_manager.py:409` | 2 | 50% | 4 | 🟡 частично |  |
| `JobManager.unsubscribe_all` | `job_manager.py:412` | 2 | 50% | 4 | 🟡 частично |  |
| `JobManager._notify` | `job_manager.py:417` | 44 | 16% | 35 | 🟡 частично | Publish job state to SSE subscribers via NotificationHub. Progress events omit log_lines ( |
| `JobManager.add_string_update` | `job_manager.py:464` | 18 | 28% | 12 | 🟡 частично | Append a per-string translation result and broadcast via SSE. |
| `JobManager.increment_progress_from_dispatch` | `job_manager.py:483` | 19 | 5% | 2 | 🟠 не покрыт | Called when a dispatch waiter receives a hash result from another job. Appends the string  |
| `JobManager.update_progress` | `job_manager.py:503` | 21 | 81% | 33 | 🟢 покрыт |  |
| `JobManager._persist` | `job_manager.py:527` | 29 | 72% | 20 | 🟢 покрыт |  |
| `JobManager._load_persisted` | `job_manager.py:557` | 39 | 38% | 4 | 🟡 частично |  |

### Реестр воркеров и wire

| Функция / метод | Файл:строка | loc | Покрытие | Ссылок | Статус | Назначение |
|---|---|---|---|---|---|---|
| `hello` | `protocol.py:53` | 2 | 100% | 16 | 🟢 покрыт |  |
| `command` | `protocol.py:57` | 2 | 100% | 45 | 🟢 покрыт |  |
| `result` | `protocol.py:61` | 2 | 100% | 525 | 🟢 покрыт |  |
| `telemetry` | `protocol.py:65` | 2 | 100% | 21 | 🟢 покрыт |  |
| `ping` | `protocol.py:69` | 1 | 100% | 16 | 🟢 покрыт |  |
| `pong` | `protocol.py:70` | 1 | 100% | 10 | 🟢 покрыт |  |
| `bye` | `protocol.py:71` | 1 | 100% | 6 | 🟢 покрыт |  |
| `validate` | `protocol.py:75` | 13 | 77% | 24 | 🟢 покрыт | Return (ok, error). A message is valid if it's a dict with a known `type` and all the requ |
| `encode` | `protocol.py:90` | 3 | 67% | 33 | 🟢 покрыт | Serialize a message to a wire line (newline-terminated JSON). |
| `decode_line` | `protocol.py:95` | 8 | 88% | 11 | 🟢 покрыт | Parse one wire line → validated message dict, or None if malformed/invalid. |
| `_send` | `agent_hub.py:45` | 2 | 100% | 4 | 🟢 покрыт |  |
| `_Conn.__init__` | `agent_hub.py:52` | 6 | 100% | 75 | 🟢 покрыт |  |
| `AgentHub.__init__` | `agent_hub.py:61` | 17 | 82% | 75 | 🟢 покрыт |  |
| `AgentHub.start` | `agent_hub.py:80` | 15 | 87% | 80 | 🟢 покрыт | Bind, listen, and spawn accept + keepalive loops. Returns the bound port (useful when port |
| `AgentHub.stop` | `agent_hub.py:96` | 14 | 57% | 18 | 🟡 частично |  |
| `AgentHub._accept_loop` | `agent_hub.py:112` | 9 | 100% | 1 | 🟢 покрыт |  |
| `AgentHub._serve_conn` | `agent_hub.py:122` | 72 | 67% | 1 | 🟢 покрыт |  |
| `AgentHub.push` | `agent_hub.py:196` | 17 | 47% | 51 | 🟡 частично | Push a message to an agent over its held-open connection. Returns False if the agent is no |
| `AgentHub.command` | `agent_hub.py:214` | 4 | 75% | 45 | 🟢 покрыт |  |
| `AgentHub._write` | `agent_hub.py:219` | 3 | 100% | 3 | 🟢 покрыт |  |
| `AgentHub.connected_labels` | `agent_hub.py:224` | 3 | 100% | 5 | 🟢 покрыт |  |
| `AgentHub.is_connected` | `agent_hub.py:228` | 4 | 100% | 1 | 🟢 покрыт |  |
| `AgentHub._keepalive_loop` | `agent_hub.py:233` | 26 | 35% | 1 | 🟡 частично |  |
| `BackendWorkerStatus.to_dict` | `worker_pool.py:37` | 10 | 10% | 36 | 🟠 не покрыт |  |
| `WorkerPool.__init__` | `worker_pool.py:58` | 3 | 100% | 75 | 🟢 покрыт |  |
| `WorkerPool.run` | `worker_pool.py:62` | 179 | 39% | 190 | 🟡 частично | Distribute *strings* across all backends and block until all done. Returns {"done": N, "er |
| `WorkerInfo.to_dict` | `worker_registry.py:49` | 23 | 9% | 36 | 🟠 не покрыт |  |
| `WorkerRegistry.__init__` | `worker_registry.py:83` | 27 | 59% | 75 | 🟡 частично |  |
| `WorkerRegistry.subscribe` | `worker_registry.py:111` | 6 | 83% | 14 | 🟢 покрыт | Register an SSE subscriber. Returns a queue that gets a token on every change. |
| `WorkerRegistry.unsubscribe` | `worker_registry.py:118` | 3 | 100% | 8 | 🟢 покрыт |  |
| `WorkerRegistry._publish` | `worker_registry.py:122` | 9 | 89% | 9 | 🟢 покрыт | Signal all subscribers that worker state changed (coalesced — just a token). |
| `WorkerRegistry.request_resend` | `worker_registry.py:132` | 6 | 67% | 4 | 🟢 покрыт | Ask an agent (incl. NAT/pull-mode) to re-deliver results with seq > `since`. Used by rebui |
| `WorkerRegistry.take_resend` | `worker_registry.py:139` | 4 | 75% | 4 | 🟢 покрыт | Pop a pending resend request for an agent (called by the heartbeat handler). |
| `WorkerRegistry.register` | `worker_registry.py:146` | 19 | 47% | 63 | 🟡 частично | Register or update a worker. last_seen is set to now. If the worker was in 'restarting' OT |
| `WorkerRegistry.heartbeat` | `worker_registry.py:166` | 79 | 49% | 82 | 🟡 частично | Update last_seen and any pushed fields. Returns (found, lost_job_ids): - found: False if u |
| `WorkerRegistry._package_exists` | `worker_registry.py:246` | 5 | 20% | 1 | 🟡 частично | Return True if the persisted package file still exists on disk. |
| `WorkerRegistry.remove` | `worker_registry.py:252` | 4 | 100% | 10 | 🟢 покрыт |  |
| `WorkerRegistry.update_task` | `worker_registry.py:257` | 6 | 100% | 5 | 🟢 покрыт |  |
| `WorkerRegistry.get` | `worker_registry.py:266` | 3 | 100% | 1491 | 🟢 покрыт |  |
| `WorkerRegistry.get_active` | `worker_registry.py:270` | 5 | 80% | 10 | 🟢 покрыт | Return workers that sent a heartbeat within HEARTBEAT_TTL seconds. |
| `WorkerRegistry.get_all` | `worker_registry.py:276` | 3 | 100% | 8 | 🟢 покрыт |  |
| `WorkerRegistry.enqueue_chunk` | `worker_registry.py:282` | 14 | 50% | 27 | 🟡 частично | Put a work chunk into the worker's queue for pull-mode remotes. Offline packages (type='of |
| `WorkerRegistry.dequeue_chunk` | `worker_registry.py:297` | 28 | 79% | 8 | 🟢 покрыт | Called by the GET /api/workers/<label>/chunk endpoint. Blocks up to `timeout` seconds wait |
| `WorkerRegistry.cancel_queued_chunk` | `worker_registry.py:326` | 4 | 25% | 4 | 🟡 частично | Mark a chunk_id as cancelled so it is silently dropped when dequeued. |
| `WorkerRegistry._persist_package` | `worker_registry.py:333` | 14 | 7% | 1 | 🟠 не покрыт | Write an offline package to disk so it survives server restarts. |
| `WorkerRegistry._delete_package` | `worker_registry.py:348` | 9 | 33% | 1 | 🟡 частично | Remove a persisted offline package after successful delivery. |
| `WorkerRegistry._restore_persisted_packages` | `worker_registry.py:358` | 40 | 12% | 1 | 🟡 частично | On startup: reload persisted offline packages into in-memory queues. Called once from __in |
| `WorkerRegistry.register_chunk_wait` | `worker_registry.py:399` | 11 | 64% | 2 | 🟢 покрыт | Register that the host is waiting for a result for chunk_id. Returns an event that will be |
| `WorkerRegistry.deliver_result` | `worker_registry.py:411` | 10 | 80% | 11 | 🟢 покрыт | Called when remote POSTs a result. Sets the waiting event. Returns True if someone was wai |
| `WorkerRegistry.collect_result` | `worker_registry.py:422` | 9 | 78% | 25 | 🟢 покрыт | Block until result arrives or timeout. Cleans up internal state. Returns the raw inference |
| `WorkerRegistry.collect_result_poll` | `worker_registry.py:432` | 25 | 48% | 1 | 🟡 частично | Like collect_result but calls poll_cb() every poll_interval seconds. Use for long-running  |
| `WorkerRegistry.register_offline_job` | `worker_registry.py:460` | 20 | 25% | 28 | 🟡 частично | Register a dispatched offline job. Called once per worker. |
| `WorkerRegistry.update_offline_progress` | `worker_registry.py:481` | 14 | 79% | 8 | 🟢 покрыт |  |
| `WorkerRegistry.get_offline_jobs_for_host_job` | `worker_registry.py:496` | 4 | 75% | 3 | 🟢 покрыт |  |
| `WorkerRegistry.finish_offline_job` | `worker_registry.py:501` | 14 | 79% | 13 | 🟢 покрыт | Mark one worker's offline job as done. Returns True if ALL workers for the host job are no |
| `WorkerRegistry._offline_jobs_snapshot` | `worker_registry.py:516` | 4 | 75% | 2 | 🟢 покрыт | Thread-safe snapshot of all offline job records. |
| `WorkerRegistry.delete_offline_package` | `worker_registry.py:521` | 6 | 83% | 6 | 🟢 покрыт | Delete the persisted package file for an offline job (call when done=True arrives). |
| `WorkerRegistry.get_offline_job` | `worker_registry.py:528` | 3 | 100% | 15 | 🟢 покрыт |  |

### Оффлайн-диспатч

| Функция / метод | Файл:строка | loc | Покрытие | Ссылок | Статус | Назначение |
|---|---|---|---|---|---|---|
| `next_unassigned_batch` | `auto_feed.py:25` | 31 | 36% | 7 | 🟡 частично | The next `limit` PENDING strings not already covered by an active assignment. Returns dict |
| `feed_once` | `auto_feed.py:58` | 55 | 31% | 3 | 🟡 частично | One feeder sweep: give each idle live worker a fresh batch. Returns total strings dispatch |
| `feed_loop` | `auto_feed.py:115` | 13 | 38% | 2 | 🟡 частично | Background sweep; acts only while app.config['AUTO_FEED']['enabled'] is True. |
| `_build_tm_pairs` | `offline_backend.py:24` | 17 | 35% | 2 | 🟡 частично | Build a capped {original: translation} dict from the DB for the mod. |
| `_build_terminology` | `offline_backend.py:43` | 7 | 14% | 10 | 🟡 частично | Build relevant Skyrim terminology block for the given originals. |
| `_split_round_robin` | `offline_backend.py:52` | 7 | 86% | 13 | 🟢 покрыт | Sort by original length desc, then assign round-robin across workers (fallback). |
| `_is_long` | `offline_backend.py:66` | 4 | 100% | 8 | 🟢 покрыт |  |
| `_agent_meta` | `offline_backend.py:72` | 12 | 83% | 2 | 🟢 покрыт | Per-agent weight (throughput) + capability (VRAM/RAM ≈ model size it can run), derived fro |
| `smart_partition` | `offline_backend.py:86` | 32 | 72% | 8 | 🟢 покрыт | Assign strings to agents so that (G7) faster agents get proportionally more work, and (G5) |
| `_make_remote_strings` | `offline_backend.py:120` | 25 | 48% | 10 | 🟡 частично | Build the remote payload string dicts AND the host-side manifest items, sharing one string |
| `_persist_host_assignment` | `offline_backend.py:147` | 13 | 38% | 8 | 🟡 частично | Record a durable host-side assignment + manifest so recovery/reassignment and delivery tra |
| `dispatch` | `offline_backend.py:162` | 106 | 33% | 124 | 🟡 частично | Package strings and dispatch to one or more remote workers. Transitions job.status to OFFL |
| `dispatch_multi` | `offline_backend.py:270` | 125 | 1% | 13 | 🟠 не покрыт | Package strings from multiple mods and dispatch to remote workers. Each mod gets its own c |
| `get_pull_stats` | `pull_backend.py:33` | 12 | 33% | 7 | 🟡 частично | Return a snapshot of session-level pull-mode inference stats. |
| `reset_pull_stats` | `pull_backend.py:47` | 5 | 80% | 6 | 🟢 покрыт | Reset all session-level pull-mode stats. |
| `RegistryPullBackend.__init__` | `pull_backend.py:68` | 13 | 46% | 75 | 🟡 частично |  |
| `RegistryPullBackend.translate` | `pull_backend.py:82` | 152 | 36% | 205 | 🟡 частично | Translate *texts* via the pull-mode remote worker. Batches are sent as individual chunk jo |
| `apply_pulled_results` | `pull_reconcile.py:27` | 38 | 74% | 12 | 🟢 покрыт | Apply a page of pulled results to the canonical DB. Pure w.r.t. transport, so it is unit-t |
| `reconcile_agent` | `pull_reconcile.py:67` | 47 | 2% | 4 | 🟠 не покрыт | Pull and reconcile one reachable agent. Returns number of results applied. Silently skips  |
| `pull_loop` | `pull_reconcile.py:116` | 16 | 31% | 2 | 🟡 частично | Background sweep: reconcile every known agent on an interval. |
| `_resolve_active_backends` | `redispatch.py:20` | 14 | 64% | 1 | 🟢 покрыт | (label, RegistryPullBackend) for every currently-alive worker — no current_app, so this is |
| `gather_reassignable` | `redispatch.py:36` | 24 | 58% | 3 | 🟡 частично | {mod_name: [string_dict,...]} for the still-PENDING undelivered strings of orphaned assign |
| `_close_orphaned` | `redispatch.py:62` | 7 | 86% | 4 | 🟢 покрыт | Mark orphaned assignments 'failed' (closed) so their strings aren't re-picked. |
| `auto_redispatch` | `redispatch.py:71` | 51 | 31% | 9 | 🟡 частично | Re-dispatch orphaned pending work to live workers. Returns the new job id, or None if ther |

### Управление моделями фло­та

| Функция / метод | Файл:строка | loc | Покрытие | Ссылок | Статус | Назначение |
|---|---|---|---|---|---|---|
| `get_entry` | `model_catalog.py:67` | 2 | 100% | 6 | 🟢 покрыт |  |
| `enrich` | `model_catalog.py:71` | 12 | 33% | 3 | 🟡 частично | Attach a memory/context estimate to a catalog entry. |
| `catalog` | `model_catalog.py:85` | 4 | 50% | 16 | 🟡 частично | Full catalog, each entry enriched with an estimate at its default context (and a fit verdi |
| `estimate_kv_cache_mb` | `model_estimator.py:22` | 7 | 57% | 4 | 🟡 частично | Approx KV-cache size in MB for a given context window. |
| `estimate_total_vram_mb` | `model_estimator.py:31` | 12 | 33% | 3 | 🟡 частично | Total estimated VRAM = weights + KV cache + overhead. Returns a breakdown. |
| `max_n_ctx_for_vram` | `model_estimator.py:45` | 13 | 62% | 5 | 🟢 покрыт | Largest context window whose weights+KV+overhead fit in vram_mb. 0 if weights alone don't  |
| `fit` | `model_estimator.py:60` | 12 | 75% | 39 | 🟢 покрыт | Classify fit: 'no' (weights alone don't fit), 'tight' (<10% headroom), 'full'. |
| `estimate` | `model_estimator.py:74` | 17 | 53% | 39 | 🟡 частично | Full estimate for a model at a context size, optionally judged against an agent's VRAM. Un |
| `create_session` | `model_staging.py:21` | 8 | 0% | 2 | 🟠 не покрыт |  |
| `set_session_root` | `model_staging.py:31` | 8 | 0% | 1 | 🟠 не покрыт | Override the directory used to serve files for this session. Call this after downloading m |
| `get_session_path` | `model_staging.py:41` | 3 | 0% | 1 | 🟠 не покрыт |  |
| `delete_session` | `model_staging.py:46` | 6 | 0% | 4 | 🟠 не покрыт |  |
| `_sanitize_spec` | `model_state.py:54` | 2 | 100% | 3 | 🟢 покрыт |  |
| `model_matches` | `model_state.py:58` | 17 | 47% | 7 | 🟡 частично | Is the agent's currently-loaded model the one `spec` asks for? Agents report a model label |
| `ModelStateManager.__init__` | `model_state.py:81` | 8 | 75% | 75 | 🟢 покрыт |  |
| `ModelStateManager._load_defaults` | `model_state.py:91` | 10 | 60% | 1 | 🟢 покрыт |  |
| `ModelStateManager._save_defaults_nolock` | `model_state.py:102` | 11 | 64% | 3 | 🟢 покрыт |  |
| `ModelStateManager.set_default` | `model_state.py:114` | 17 | 47% | 10 | 🟡 частично | Record the durable default model for an agent (called on every explicit UI load). Lifts an |
| `ModelStateManager.get_default` | `model_state.py:132` | 4 | 100% | 4 | 🟢 покрыт |  |
| `ModelStateManager.get_all_defaults` | `model_state.py:137` | 3 | 33% | 1 | 🟡 частично |  |
| `ModelStateManager.suspend_default` | `model_state.py:141` | 14 | 64% | 3 | 🟢 покрыт | Explicit unload: keep the default on file but stop auto-heal until the next explicit load  |
| `ModelStateManager.clear_default` | `model_state.py:156` | 9 | 89% | 4 | 🟢 покрыт |  |
| `ModelStateManager._materialize_default_nolock` | `model_state.py:166` | 17 | 65% | 1 | 🟢 покрыт | If the agent has NO model loaded, no active desire, and an unsuspended default on file, tu |
| `ModelStateManager.set_desired` | `model_state.py:185` | 6 | 50% | 8 | 🟡 частично |  |
| `ModelStateManager.get_desired` | `model_state.py:192` | 4 | 25% | 0 | 🔴 мёртвый |  |
| `ModelStateManager.clear` | `model_state.py:197` | 9 | 78% | 26 | 🟢 покрыт |  |
| `ModelStateManager._satisfied_nolock` | `model_state.py:208` | 3 | 100% | 3 | 🟢 покрыт |  |
| `ModelStateManager.is_satisfied` | `model_state.py:212` | 6 | 100% | 5 | 🟢 покрыт |  |
| `ModelStateManager.all_satisfied` | `model_state.py:219` | 2 | 100% | 4 | 🟢 покрыт |  |
| `ModelStateManager.pending` | `model_state.py:222` | 2 | 50% | 321 | 🟡 частично |  |
| `ModelStateManager._enqueue_load_nolock` | `model_state.py:226` | 19 | 74% | 2 | 🟢 покрыт |  |
| `ModelStateManager.dispatch_all` | `model_state.py:246` | 11 | 82% | 6 | 🟢 покрыт | A — initial parallel fan-out. Enqueues a load for every agent not already on the desired m |
| `ModelStateManager.reconcile` | `model_state.py:258` | 19 | 79% | 41 | 🟢 покрыт | B — called on each heartbeat. If the agent has diverged from its desired model and nothing |

### Резервирование работы

| Функция / метод | Файл:строка | loc | Покрытие | Ссылок | Статус | Назначение |
|---|---|---|---|---|---|---|
| `HashDispatchPool.__init__` | `hash_dispatch_pool.py:32` | 2 | 100% | 75 | 🟢 покрыт |  |
| `HashDispatchPool.claim_batch` | `hash_dispatch_pool.py:37` | 79 | 34% | 11 | 🟡 частично | Atomically claim a batch of string hashes. For each hash: - Not in pool or status='queued' |
| `HashDispatchPool.complete_hash` | `hash_dispatch_pool.py:119` | 41 | 29% | 7 | 🟡 частично | Mark a hash as done and return all registered waiters. The waiter rows are deleted atomica |
| `HashDispatchPool.release_job` | `hash_dispatch_pool.py:163` | 19 | 47% | 5 | 🟡 частично | Reset owned 'translating' hashes back to 'queued' and remove this job's waiter rows. Calle |
| `HashDispatchPool.release_all_translating` | `hash_dispatch_pool.py:183` | 46 | 28% | 2 | 🟡 частично | Reset all 'translating' hashes to 'queued' on server startup. OFFLINE_DISPATCHED jobs may  |
| `HashDispatchPool.get_pending_waiters` | `hash_dispatch_pool.py:232` | 7 | 43% | 6 | 🟡 частично | Return the number of hashes this job is still waiting on. |

### Статистика и оценки

| Функция / метод | Файл:строка | loc | Покрытие | Ссылок | Статус | Назначение |
|---|---|---|---|---|---|---|
| `compute_mod_status` | `stats_manager.py:16` | 17 | 47% | 1 | 🟡 частично | Shared status helper — single source of truth for mod status strings. |
| `StatsManager.__init__` | `stats_manager.py:68` | 6 | 33% | 75 | 🟡 частично | Args: db: TranslationDB instance |
| `StatsManager.get_mod_stats` | `stats_manager.py:77` | 21 | 29% | 4 | 🟡 частично | Return stats for a mod. Recomputes if cache is missing or stale. |
| `StatsManager.get_all_stats` | `stats_manager.py:99` | 18 | 6% | 5 | 🟠 не покрыт | Return all mod stats from cache (single SELECT, no COUNT(*)). |
| `StatsManager.get_global_stats` | `stats_manager.py:118` | 30 | 3% | 1 | 🟠 не покрыт | Aggregate statistics across all mod_stats_cache rows. |
| `StatsManager.save_validation_result` | `stats_manager.py:149` | 12 | 8% | 2 | 🟠 не покрыт | Persist validation result into mod_stats_cache.validation_issues_count. Creates a minimal  |
| `StatsManager.invalidate` | `stats_manager.py:164` | 12 | 33% | 30 | 🟡 частично | Remove cache entries so next read triggers a recompute. Cheap — just deletes from cache ta |
| `StatsManager.recompute` | `stats_manager.py:177` | 16 | 19% | 32 | 🟡 частично | Run COUNT(*) GROUP BY and upsert mod_stats_cache. Also counts active reservations via JOIN |
| `StatsManager._recompute_one` | `stats_manager.py:194` | 36 | 14% | 2 | 🟡 частично |  |
| `StatsManager._row_to_stats` | `stats_manager.py:234` | 30 | 33% | 4 | 🟡 частично |  |
| `_fmt_duration` | `campaign.py:10` | 12 | 100% | 5 | 🟢 покрыт |  |
| `estimate_campaign` | `campaign.py:24` | 24 | 38% | 7 | 🟡 частично | Estimate wall-clock to translate `pending` strings of mean length `avg_chars` across a fle |
| `classify_size` | `quality_profiles.py:45` | 7 | 100% | 5 | 🟢 покрыт |  |
| `_model_spec` | `quality_profiles.py:54` | 12 | 33% | 1 | 🟡 частично |  |
| `plan_phases` | `quality_profiles.py:68` | 23 | 52% | 5 | 🟡 частично | Group strings into ordered phases (small → medium → large). Each phase carries the model + |
| `summarize_plan` | `quality_profiles.py:93` | 20 | 25% | 7 | 🟡 частично | Plan preview for the UI: how many strings per tier/model, in execution order, and how many |

### Кэши

| Функция / метод | Файл:строка | loc | Покрытие | Ссылок | Статус | Назначение |
|---|---|---|---|---|---|---|
| `BsaStringCache.__init__` | `asset_cache.py:43` | 3 | 100% | 75 | 🟢 покрыт |  |
| `BsaStringCache._cache_dir` | `asset_cache.py:47` | 2 | 50% | 16 | 🟡 частично |  |
| `BsaStringCache._is_stale` | `asset_cache.py:50` | 7 | 14% | 3 | 🟡 частично |  |
| `BsaStringCache.available` | `asset_cache.py:58` | 2 | 50% | 55 | 🟡 частично |  |
| `BsaStringCache.ensure_extracted` | `asset_cache.py:61` | 54 | 2% | 7 | 🟠 не покрыт | Extract MCM txt files from a BSA into the cache. Returns cache dir on success (even if emp |
| `BsaStringCache.get_english_files` | `asset_cache.py:116` | 5 | 20% | 5 | 🟡 частично |  |
| `BsaStringCache.russian_path_for` | `asset_cache.py:122` | 4 | 25% | 3 | 🟡 частично | Return the expected *_russian.txt path for a cached *_english.txt. |
| `BsaStringCache.apply_to_bsa` | `asset_cache.py:127` | 63 | 2% | 1 | 🟠 не покрыт | Unpack BSA, overlay *_russian.txt from cache, repack in-place. Backup is created before re |
| `SwfStringCache.__init__` | `asset_cache.py:200` | 3 | 100% | 75 | 🟢 покрыт |  |
| `SwfStringCache._cache_dir` | `asset_cache.py:204` | 3 | 33% | 16 | 🟡 частично |  |
| `SwfStringCache._is_stale` | `asset_cache.py:208` | 7 | 14% | 3 | 🟡 частично |  |
| `SwfStringCache.available` | `asset_cache.py:216` | 8 | 12% | 55 | 🟡 частично |  |
| `SwfStringCache.ensure_extracted` | `asset_cache.py:225` | 51 | 2% | 7 | 🟠 не покрыт | Export text strings from SWF into cache using FFDec. Exported files are renamed {chid}.txt |
| `SwfStringCache.get_english_files` | `asset_cache.py:277` | 6 | 17% | 5 | 🟡 частично | Return all {chid}_en.txt files in the cache for this SWF. |
| `SwfStringCache.russian_path_for` | `asset_cache.py:284` | 4 | 25% | 3 | 🟡 частично | Return the {chid}_ru.txt path for a given {chid}_en.txt. |
| `SwfStringCache.get_text_files` | `asset_cache.py:289` | 6 | 17% | 0 | 🔴 мёртвый | Legacy: return all txt files (kept for compatibility). |
| `SwfStringCache.apply_to_swf` | `asset_cache.py:296` | 60 | 2% | 1 | 🟠 не покрыт | Reimport edited text files from cache back into the SWF using FFDec. Backup is created bef |
| `GlobalTextDict.__init__` | `global_dict.py:34` | 15 | 53% | 75 | 🟡 частично |  |
| `GlobalTextDict.load` | `global_dict.py:52` | 20 | 55% | 166 | 🟡 частично | Load dictionary (no-op if already loaded). From SQLite when a DB is set, else JSON. |
| `GlobalTextDict._load_from_db` | `global_dict.py:73` | 20 | 60% | 1 | 🟢 покрыт |  |
| `GlobalTextDict.get` | `global_dict.py:94` | 5 | 80% | 1491 | 🟢 покрыт | Return existing translation for an exact original string, or None. |
| `GlobalTextDict.get_batch` | `global_dict.py:100` | 5 | 60% | 1 | 🟢 покрыт | Return {original: translation} for all originals found in dict. |
| `GlobalTextDict.add` | `global_dict.py:106` | 5 | 80% | 57 | 🟢 покрыт | Record a new translation (in-memory only; call save() to persist). |
| `GlobalTextDict.save` | `global_dict.py:112` | 24 | 46% | 40 | 🟡 частично | Persist the dictionary. SQLite upsert when a DB is set (merges by construction — ON CONFLI |
| `GlobalTextDict._persist_db` | `global_dict.py:137` | 15 | 47% | 2 | 🟡 частично |  |
| `GlobalTextDict.size` | `global_dict.py:153` | 2 | 50% | 265 | 🟡 частично |  |
| `GlobalTextDict.rebuild` | `global_dict.py:156` | 75 | 1% | 17 | 🟠 не покрыт | Full rescan: read every .trans.json under mods_dir, pick the most common translation for e |

### Сканер модов

| Функция / метод | Файл:строка | loc | Покрытие | Ссылок | Статус | Назначение |
|---|---|---|---|---|---|---|
| `ModInfo.pct` | `mod_scanner.py:53` | 4 | 25% | 95 | 🟡 частично |  |
| `ModInfo.to_dict` | `mod_scanner.py:58` | 5 | 20% | 36 | 🟡 частично |  |
| `ModScanner.__init__` | `mod_scanner.py:68` | 21 | 57% | 75 | 🟡 частично |  |
| `ModScanner.scan_all` | `mod_scanner.py:92` | 37 | 3% | 7 | 🟠 не покрыт | Full scan of all mods_dirs. Returns sorted list of ModInfo. Results are cached for SCAN_TT |
| `ModScanner.invalidate` | `mod_scanner.py:130` | 10 | 10% | 30 | 🟠 не покрыт | Bust the scan cache. Pass a mod folder name to evict just one entry, or None to force a fu |
| `ModScanner.get_mod_path` | `mod_scanner.py:141` | 12 | 8% | 26 | 🟠 не покрыт | Return the full path to a mod folder, searching all mods_dirs. |
| `ModScanner.get_mod` | `mod_scanner.py:154` | 23 | 4% | 8 | 🟠 не покрыт |  |
| `ModScanner.get_mod_strings` | `mod_scanner.py:178` | 239 | 0% | 3 | 🟠 не покрыт | Extract strings from ESP/ESM, loose MCM, BSA-embedded MCM, and SWF. Returns list of dicts: |
| `ModScanner.get_stats` | `mod_scanner.py:418` | 26 | 4% | 3 | 🟠 не покрыт | Aggregate stats across all cached mods. |
| `ModScanner._scan_mod` | `mod_scanner.py:447` | 131 | 1% | 2 | 🟠 не покрыт |  |
| `ModScanner._patch_stats_from_stats_mgr` | `mod_scanner.py:579` | 18 | 6% | 3 | 🟠 не покрыт | Bulk-patch translation stats from StatsManager (materialized cache). |
| `ModScanner._patch_stats_from_db` | `mod_scanner.py:598` | 10 | 10% | 1 | 🟠 не покрыт | Bulk-patch translation stats on cached ModInfo objects from SQLite. |
| `ModScanner._patch_mod_stats_from_db` | `mod_scanner.py:609` | 7 | 14% | 2 | 🟡 частично | Patch translation stats on a single cached ModInfo from SQLite. |
| `ModScanner._apply_stats` | `mod_scanner.py:618` | 25 | 4% | 4 | 🟠 не покрыт |  |
| `ModScanner.scan_string_counts` | `mod_scanner.py:644` | 128 | 1% | 1 | 🟠 не покрыт | Explicit (user-triggered) deep scan: parse ESP + BSA/MCM + SWF files and cache string coun |
| `ModScanner._load_translation_cache` | `mod_scanner.py:773` | 7 | 14% | 3 | 🟡 частично |  |
| `ModScanner._load_counts_cache` | `mod_scanner.py:781` | 7 | 14% | 4 | 🟡 частично |  |
| `ModScanner._save_counts_cache` | `mod_scanner.py:789` | 7 | 14% | 1 | 🟡 частично |  |
| `ModScanner._count_esp_strings` | `mod_scanner.py:797` | 11 | 9% | 1 | 🟠 не покрыт | Parse ESP and return (total, untranslatable) string counts. |
| `ModScanner.file_hash` | `mod_scanner.py:809` | 12 | 8% | 1 | 🟠 не покрыт |  |
| `_read_nexus_id` | `mod_scanner.py:823` | 9 | 11% | 1 | 🟠 не покрыт |  |
| `_check_localized` | `mod_scanner.py:834` | 11 | 9% | 1 | 🟠 не покрыт | Read TES4 flags bit 0x80 — if set, the plugin uses external .STRINGS files. |

### HTTP-маршруты

| Функция / метод | Файл:строка | loc | Покрытие | Ссылок | Статус | Назначение |
|---|---|---|---|---|---|---|
| `register_routes` | `__init__.py:5` | 26 | 96% | 2 | 🟢 покрыт |  |
| `_is_lan_url` | `api.py:14` | 22 | 73% | 10 | 🟢 покрыт | True only if `url`'s host resolves to a private/LAN/loopback address. Used to block SSRF o |
| `setup_reports` | `api.py:39` | 3 | 33% | 0 | 🟡 частично |  |
| `clear_setup_reports` | `api.py:45` | 3 | 33% | 0 | 🟡 частично |  |
| `stats` | `api.py:51` | 21 | 5% | 190 | 🟠 не покрыт |  |
| `mods` | `api.py:75` | 34 | 3% | 357 | 🟠 не покрыт |  |
| `mod_info_by_id` | `api.py:112` | 13 | 8% | 0 | 🟠 не покрыт | Resolve a numeric mod ID to full ModInfo. Used by ID-based frontend routes. |
| `mod_info` | `api.py:128` | 6 | 17% | 0 | 🟡 частично |  |
| `reset_translations` | `api.py:137` | 42 | 2% | 0 | 🟠 не покрыт | Reset all translatable ESP strings to pending (translation=null, status=pending, score=nul |
| `fix_untranslatable` | `api.py:182` | 47 | 2% | 0 | 🟠 не покрыт | Set translation=original, score=100, status=translated for all untranslatable strings. |
| `jobs` | `api.py:232` | 10 | 10% | 310 | 🟠 не покрыт |  |
| `job_detail` | `api.py:245` | 6 | 17% | 1 | 🟡 частично |  |
| `job_logs` | `api.py:254` | 10 | 10% | 0 | 🟠 не покрыт |  |
| `gpu_info` | `api.py:267` | 21 | 5% | 3 | 🟠 не покрыт |  |
| `nexus_test` | `api.py:291` | 16 | 6% | 0 | 🟠 не покрыт |  |
| `mod_context_api` | `api.py:310` | 49 | 2% | 0 | 🟠 не покрыт | Return the AI context for a mod. Returns both: auto_context — BART/LLM summary of Nexus de |
| `mod_nexus_raw` | `api.py:362` | 42 | 2% | 0 | 🟠 не покрыт | Return the raw Nexus mod description from disk cache (no API call). Returns {ok, mod_id, n |
| `mod_nexus_fetch` | `api.py:407` | 48 | 2% | 0 | 🟠 не покрыт | Synchronously fetch (or re-fetch) the raw Nexus description for a mod. Fast — no LLM invol |
| `save_mod_context` | `api.py:458` | 16 | 6% | 0 | 🟠 не покрыт | Save custom context text for a mod. |
| `token_stats` | `api.py:477` | 19 | 5% | 0 | 🟠 не покрыт | Return cumulative token usage across all translation calls this session. Merges local (lla |
| `token_reset` | `api.py:499` | 17 | 6% | 0 | 🟠 не покрыт |  |
| `mod_validation` | `api.py:519` | 15 | 7% | 0 | 🟠 не покрыт | Return saved validation results for a mod. |
| `models_status` | `api.py:537` | 32 | 3% | 0 | 🟠 не покрыт | Return status of AI models (loaded / not loaded / downloading). |
| `servers_test` | `api.py:572` | 37 | 3% | 0 | 🟠 не покрыт | Test a remote server — uses registry cache for pull-mode workers (no inbound connection).  |
| `servers` | `api.py:612` | 8 | 12% | 57 | 🟡 частично | Return last known list of discovered LAN translation servers. |
| `servers_scan` | `api.py:623` | 4 | 25% | 0 | 🟡 частично | Trigger a background LAN scan. |
| `remote_config_get` | `api.py:630` | 49 | 2% | 0 | 🟠 не покрыт | Return current remote/local backend configuration and model info. |
| `remote_stats` | `api.py:682` | 23 | 4% | 0 | 🟠 не покрыт | Return stats for the configured remote server from registry cache (no inbound connection). |
| `tokens_perf` | `api.py:708` | 44 | 2% | 0 | 🟠 не покрыт | Return merged performance stats from all inference sources. |
| `translate_one_string` | `api.py:755` | 322 | 0% | 0 | 🟠 не покрыт | Synchronously translate a single string via AI. Body: {key, esp, original} Returns: {ok, t |
| `global_dict_stats` | `api.py:1080` | 10 | 10% | 0 | 🟠 не покрыт | Return global text dictionary statistics. |
| `global_dict_rebuild` | `api.py:1093` | 19 | 5% | 0 | 🟠 не покрыт | Trigger a background rebuild of the global text dictionary. |
| `global_dict_toggle` | `api.py:1115` | 30 | 3% | 0 | 🟠 не покрыт | Enable or disable use_global_dict in config.yaml. |
| `workers_list` | `api.py:1148` | 50 | 28% | 0 | 🟡 частично | Return all registered remote workers (active + recently seen). Each worker's offline_jobs  |
| `workers_stream` | `api.py:1201` | 33 | 3% | 0 | 🟠 не покрыт | SSE stream of worker/agent state — pushed on every change (registry pub/sub), so the brows |
| `workers_register` | `api.py:1237` | 52 | 50% | 0 | 🟡 частично | Remote server calls this on startup to announce itself. |
| `workers_heartbeat` | `api.py:1292` | 119 | 29% | 0 | 🟡 частично | Remote server calls this every ~15 s to stay alive in the registry. Optionally accepts 'mo |
| `workers_unregister` | `api.py:1414` | 7 | 14% | 0 | 🟡 частично | Remote server calls this on clean shutdown. |
| `workers_cancel_offline` | `api.py:1424` | 40 | 2% | 0 | 🟠 не покрыт | Directly cancel an offline job on a worker without needing the host job. Works even when t |
| `workers_get_chunk` | `api.py:1467` | 13 | 46% | 0 | 🟡 частично | Pull-mode: remote polls for next inference chunk. Long-polls for up to `timeout` seconds ( |
| `workers_post_result` | `api.py:1483` | 15 | 67% | 0 | 🟢 покрыт | Pull-mode: remote posts completed inference result. Body: {"chunk_id": "...", "result": "r |
| `assignments_overview` | `api.py:1501` | 36 | 61% | 0 | 🟢 покрыт | Observability ledger (Phase 9): per-assignment funnel + agent liveness tier, plus aggregat |
| `models_catalog` | `api.py:1540` | 6 | 67% | 0 | 🟢 покрыт | Curated model catalog + per-default-ctx memory estimates (A3). Pass ?vram_mb= to get a fit |
| `models_estimate` | `api.py:1549` | 27 | 41% | 0 | 🟡 частично | VRAM/KV estimate + fit + max_n_ctx (A2). Either pass ?catalog_id=&n_ctx=&vram_mb= or raw ? |
| `review_queue` | `api.py:1579` | 23 | 61% | 0 | 🟢 покрыт | G11 — needs_review strings ACROSS THE WHOLE PACK, worst-quality first. Optional ?mod=, ?ma |
| `review_approve` | `api.py:1605` | 28 | 71% | 0 | 🟢 покрыт | G11 — batch-approve needs_review strings by id (cross-mod), flipping them to 'translated'. |
| `mods_priorities` | `api.py:1636` | 9 | 56% | 0 | 🟡 частично | G9 — {folder_name: priority} for all mods (UI sort/badges). |
| `set_mod_priority` | `api.py:1648` | 12 | 83% | 6 | 🟢 покрыт | G9 — set a mod's translation priority (higher = translated first by translate_all). |
| `import_translations` | `api.py:1663` | 17 | 6% | 0 | 🟠 не покрыт | A — seed a mod's strings from an existing community translation (xTranslate/SST XML). Body |
| `terminology_check` | `api.py:1683` | 21 | 5% | 0 | 🟠 не покрыт | C — glossary consistency for a mod: translated strings whose original contains a glossary  |
| `ledger_stats` | `api.py:1707` | 13 | 8% | 0 | 🟠 не покрыт | #6 — projection read from the work ledger: fleet-wide done count, unique source texts, cro |
| `translate_plan` | `api.py:1723` | 17 | 6% | 0 | 🟠 не покрыт | VM1 — preview the auto/variable-model plan for a mod's pending strings under a quality pro |
| `campaign_estimate` | `api.py:1743` | 29 | 69% | 0 | 🟢 покрыт | G8 — estimate time to finish the pending backlog across the live fleet's combined TPS. Opt |
| `models_dispatch` | `api.py:1775` | 38 | 66% | 0 | 🟢 покрыт | A4 — fan out a model download/load to several agents at once (non-blocking). Body: {model: |
| `auto_feed_status` | `api.py:1816` | 21 | 43% | 0 | 🟡 частично | Autonomous backlog draining status + how many strings remain unassigned. |
| `auto_feed_start` | `api.py:1840` | 10 | 80% | 0 | 🟢 покрыт | Turn on autonomous top-up: idle workers are continuously fed the next batch from the globa |
| `auto_feed_stop` | `api.py:1853` | 5 | 100% | 0 | 🟢 покрыт |  |
| `rebuild_from_agents` | `api.py:1861` | 31 | 42% | 0 | 🟡 частично | Recovery (Gap 5): after restoring an older master DB backup, reset all agent pull cursors  |
| `worker_abandon` | `api.py:1895` | 10 | 50% | 0 | 🟡 частично | Operator action (Phase 7): immediately orphan an agent's active assignments instead of wai |
| `workers_offline_results` | `api.py:1908` | 249 | 33% | 0 | 🟡 частично | Remote posts incremental/final results from an offline translate job. Body: { "offline_job |
| `workers_benchmark` | `api.py:2160` | 26 | 4% | 0 | 🟠 не покрыт | Run a performance benchmark on a registered worker. Enqueues a 'benchmark' chunk with stan |
| `workers_ota_step` | `api.py:2189` | 26 | 4% | 0 | 🟠 не покрыт | Worker POSTs each OTA step in real-time as it completes. Body: {"step": "git: Already up t |
| `workers_ota_update` | `api.py:2218` | 43 | 2% | 0 | 🟠 не покрыт | Trigger OTA update on a remote pull-mode worker. The remote worker streams each step back  |
| `model_transfer_file` | `api.py:2264` | 37 | 3% | 0 | 🟠 не покрыт | Stream a staged model file to the remote worker. The remote calls this endpoint (outbound: |
| `_find_in_worker_cache` | `api.py:2303` | 12 | 8% | 1 | 🟠 не покрыт | Return a cached model entry from the worker's heartbeat data, or None. |
| `_stage_mlx` | `api.py:2317` | 12 | 8% | 1 | 🟠 не покрыт |  |
| `_stage_gguf` | `api.py:2331` | 18 | 6% | 1 | 🟠 не покрыт |  |
| `_finalize_load` | `api.py:2351` | 21 | 5% | 4 | 🟠 не покрыт |  |
| `workers_model_load` | `api.py:2375` | 3 | 33% | 3 | 🟡 частично | Download + load a model on a worker (see _do_model_load). |
| `workers_model_download` | `api.py:2381` | 5 | 20% | 0 | 🟡 частично | A4 — download/stage a model on a worker WITHOUT loading it into VRAM (pre-provision). |
| `_do_model_load` | `api.py:2388` | 96 | 1% | 4 | 🟠 не покрыт | Send a load_model command to the remote worker via the pull queue. delivery (payload['deli |
| `_host_proxy_load` | `api.py:2486` | 48 | 2% | 2 | 🟠 не покрыт | Stage a model on the master and stream it to the agent (master-push / delivery=push, or th |
| `workers_model_unload` | `api.py:2537` | 31 | 3% | 0 | 🟠 не покрыт | Send an unload_model command via the pull queue. |
| `workers_get_info` | `api.py:2571` | 15 | 7% | 0 | 🟠 не покрыт | Return worker info from the registry (pushed via heartbeat). No reverse TCP connection — w |
| `workers_list_models` | `api.py:2589` | 14 | 7% | 0 | 🟠 не покрыт | Return cached .gguf files for a remote worker. Uses the models list pushed by the remote i |
| `workers_model_defaults` | `api.py:2606` | 4 | 25% | 0 | 🟡 частично | Durable per-agent default models (auto-restored when an agent comes up empty). |
| `workers_model_default` | `api.py:2613` | 13 | 8% | 0 | 🟠 не покрыт | POST — set/re-arm an agent's default model without loading it now (body = model spec, same |
| `remote_config_set` | `api.py:2629` | 43 | 2% | 0 | 🟠 не покрыт | Save remote.mode and remote.server_url to config.yaml and reload config. |
| `list_checkpoints` | `api.py:2677` | 6 | 17% | 7 | 🟡 частично |  |
| `create_checkpoint` | `api.py:2686` | 11 | 9% | 14 | 🟠 не покрыт |  |
| `restore_checkpoint` | `api.py:2700` | 8 | 12% | 6 | 🟡 частично |  |
| `delete_checkpoint` | `api.py:2711` | 6 | 17% | 6 | 🟡 частично |  |
| `get_string_history` | `api.py:2722` | 6 | 17% | 0 | 🟡 частично | Return per-string translation history. |
| `approve_string` | `api.py:2731` | 11 | 9% | 8 | 🟠 не покрыт | Promote a needs_review string to translated. |
| `approve_bulk_strings` | `api.py:2745` | 31 | 3% | 0 | 🟠 не покрыт | Approve multiple needs_review strings at once. |
| `get_string_conflicts` | `api.py:2779` | 21 | 5% | 0 | 🟠 не покрыт | Return strings where the same original has 2+ different translations in this mod. |
| `resolve_conflict` | `api.py:2803` | 35 | 3% | 0 | 🟠 не покрыт | Set all strings with a given original to a single chosen translation. |
| `get_all_mod_stats` | `api.py:2841` | 20 | 5% | 0 | 🟠 не покрыт | Return materialized stats for all mods from mod_stats_cache. |
| `recompute_stats` | `api.py:2864` | 10 | 10% | 0 | 🟠 не покрыт | Trigger a stats recompute for one mod or all mods. |
| `get_mod_reservations` | `api.py:2877` | 16 | 6% | 0 | 🟠 не покрыт | Return the strings currently in-flight for a mod — now from the live dispatch pool (string |
| `backup_list` | `backups.py:19` | 4 | 25% | 0 | 🟡 частично |  |
| `backup_list_json` | `backups.py:26` | 3 | 33% | 0 | 🟡 частично | JSON version of backup list for React SPA. |
| `create_backup` | `backups.py:32` | 32 | 3% | 0 | 🟠 не покрыт | Create a backup of mod files. |
| `restore_backup` | `backups.py:67` | 33 | 3% | 0 | 🟠 не покрыт | Restore a mod backup to its original location. |
| `delete_backup` | `backups.py:103` | 14 | 7% | 0 | 🟠 не покрыт |  |
| `restore_mod_esp` | `backups.py:120` | 80 | 1% | 0 | 🟠 не покрыт | Restore all translatable file backups (ESP, ESM, BSA, SWF) for a mod and clear caches. |
| `snapshot_trans_json` | `backups.py:203` | 42 | 2% | 0 | 🟠 не покрыт | Save a lightweight snapshot of a mod's .trans.json files (just their current state). This  |
| `list_trans_snapshots` | `backups.py:248` | 34 | 3% | 0 | 🟠 не покрыт | List all .trans.json snapshots for a mod. |
| `list_checkpoints` | `backups.py:285` | 6 | 17% | 7 | 🟡 частично |  |
| `create_checkpoint` | `backups.py:294` | 11 | 9% | 14 | 🟠 не покрыт |  |
| `restore_checkpoint` | `backups.py:308` | 6 | 17% | 6 | 🟡 частично |  |
| `delete_checkpoint` | `backups.py:317` | 6 | 17% | 6 | 🟡 частично |  |
| `_list_backups` | `backups.py:325` | 27 | 4% | 2 | 🟠 не покрыт |  |
| `config_page` | `config_rt.py:14` | 4 | 25% | 0 | 🟡 частично |  |
| `save_config` | `config_rt.py:21` | 26 | 4% | 0 | 🟠 не покрыт |  |
| `raw_config` | `config_rt.py:50` | 2 | 50% | 0 | 🟡 частично |  |
| `validate_config` | `config_rt.py:55` | 12 | 8% | 0 | 🟠 не покрыт |  |
| `_read_raw` | `config_rt.py:69` | 7 | 14% | 2 | 🟡 частично |  |
| `index` | `dashboard.py:9` | 14 | 7% | 22 | 🟠 не покрыт |  |
| `_gpu_info` | `dashboard.py:25` | 19 | 5% | 1 | 🟠 не покрыт |  |
| `job_list` | `jobs.py:19` | 11 | 9% | 0 | 🟠 не покрыт |  |
| `job_detail` | `jobs.py:33` | 8 | 12% | 1 | 🟡 частично |  |
| `job_stream` | `jobs.py:44` | 30 | 3% | 0 | 🟠 не покрыт | Server-Sent Events stream for a single job. |
| `stream_all` | `jobs.py:77` | 23 | 4% | 0 | 🟠 не покрыт | SSE stream for all job updates. |
| `create_job` | `jobs.py:103` | 76 | 1% | 0 | 🟠 не покрыт | POST /jobs/create — create a new translation job. |
| `cancel_job` | `jobs.py:182` | 14 | 7% | 0 | 🟠 не покрыт |  |
| `retry_job` | `jobs.py:199` | 42 | 2% | 0 | 🟠 не покрыт | Re-create an identical job from a failed/cancelled job's stored params. |
| `pause_job` | `jobs.py:244` | 14 | 7% | 0 | 🟠 не покрыт | Pause a running job — sets status=PAUSED, which triggers should_stop() in WorkerPool. |
| `assign_workers` | `jobs.py:261` | 32 | 3% | 0 | 🟠 не покрыт | Assign workers to a job. Auto-resumes if job is paused. |
| `unassign_workers` | `jobs.py:296` | 31 | 3% | 0 | 🟠 не покрыт | Unassign workers from a job. Auto-pauses if no workers remain and job is running. |
| `resume_job` | `jobs.py:330` | 16 | 6% | 0 | 🟠 не покрыт | Create a new job that continues where a paused/failed/cancelled translate job left off. Sk |
| `dispatch_back` | `jobs.py:349` | 128 | 1% | 1 | 🟠 не покрыт | Cancel the offline job on all assigned workers. For workers that are actively translating: |
| `dispatch_offline_from_job` | `jobs.py:480` | 59 | 2% | 0 | 🟠 не покрыт | Pause a running translate job and dispatch remaining pending strings as an offline job to  |
| `_resume_job_with_machines` | `jobs.py:541` | 11 | 9% | 3 | 🟠 не покрыт | Create a new translate_strings job using stored assigned_machines. |
| `_job_mods` | `jobs.py:554` | 18 | 61% | 6 | 🟢 покрыт | Distinct mods this job actually touched (via job_strings), falling back to params. |
| `job_tally` | `jobs.py:575` | 50 | 2% | 0 | 🟠 не покрыт | Live funnel for a job: how much was assigned, delivered, translated, pending. Survives mas |
| `collect_job` | `jobs.py:628` | 22 | 4% | 0 | 🟠 не покрыт | Deploy whatever is done — apply all translated strings for this job's mods to ESP/BSA/SWF, |
| `export_job` | `jobs.py:653` | 21 | 5% | 1 | 🟠 не покрыт | B2 — pull the DONE translations of this job as JSON (without deploying to ESP), so you can |
| `clear_finished` | `jobs.py:677` | 4 | 25% | 4 | 🟡 частично |  |
| `_create_translate_mod_job` | `jobs.py:685` | 49 | 2% | 2 | 🟠 не покрыт |  |
| `_create_batch_job` | `jobs.py:736` | 44 | 2% | 2 | 🟠 не покрыт |  |
| `_resolve_backends` | `jobs.py:782` | 39 | 3% | 8 | 🟠 не покрыт | Build a (label, backend) list from machine labels. All inference goes through registered p |
| `_collect_all_pending_mod_names` | `jobs.py:823` | 30 | 3% | 2 | 🟠 не покрыт | Return ordered list of mod folder names to process for translate_all. Mirrors the resume / |
| `_create_translate_all_job` | `jobs.py:855` | 46 | 2% | 2 | 🟠 не покрыт |  |
| `_create_apply_mod_job` | `jobs.py:903` | 24 | 4% | 3 | 🟠 не покрыт |  |
| `_create_translate_bsa_job` | `jobs.py:929` | 17 | 6% | 2 | 🟠 не покрыт |  |
| `_create_translate_strings_job` | `jobs.py:948` | 47 | 2% | 2 | 🟠 не покрыт |  |
| `_create_auto_translate_job` | `jobs.py:997` | 40 | 2% | 1 | 🟠 не покрыт | VM2/VM3 — phased auto/variable-model translation for one mod. |
| `_create_offline_translate_job` | `jobs.py:1039` | 98 | 1% | 3 | 🟠 не покрыт | Create an offline translate job — dispatches strings to remote workers autonomously. |
| `_create_offline_translate_mods_job` | `jobs.py:1139` | 123 | 1% | 3 | 🟠 не покрыт | Create a single offline translate job spanning multiple mods. All mods' pending strings ar |
| `_create_scan_job` | `jobs.py:1264` | 79 | 1% | 2 | 🟠 не покрыт |  |
| `_create_recompute_scores_job` | `jobs.py:1345` | 16 | 6% | 1 | 🟠 не покрыт |  |
| `_create_validate_job` | `jobs.py:1363` | 16 | 6% | 2 | 🟠 не покрыт |  |
| `_create_fetch_nexus_job` | `jobs.py:1381` | 19 | 5% | 2 | 🟠 не покрыт |  |
| `logs_page` | `logs_rt.py:12` | 7 | 14% | 0 | 🟡 частично |  |
| `stream_logs` | `logs_rt.py:22` | 29 | 3% | 0 | 🟠 не покрыт | SSE stream — tail the log file in real time. |
| `tail_logs` | `logs_rt.py:54` | 7 | 14% | 0 | 🟡 частично | Return last N lines of the log file as JSON (for initial load in React SPA). |
| `_tail` | `logs_rt.py:63` | 9 | 11% | 2 | 🟠 не покрыт |  |
| `mod_list` | `mods.py:13` | 13 | 8% | 0 | 🟠 не покрыт |  |
| `mod_detail` | `mods.py:29` | 11 | 9% | 1 | 🟠 не покрыт |  |
| `_filter_by_scope` | `mods.py:52` | 12 | 8% | 1 | 🟠 не покрыт |  |
| `_scope_counts` | `mods.py:66` | 10 | 10% | 1 | 🟠 не покрыт |  |
| `mod_strings` | `mods.py:79` | 103 | 1% | 2 | 🟠 не покрыт |  |
| `update_string` | `mods.py:185` | 32 | 3% | 1 | 🟠 не покрыт | Update a single translation in the cache (ESP) or russian txt (MCM). |
| `get_rec_types` | `mods.py:220` | 6 | 17% | 2 | 🟡 частично | Return distinct record types for the record-type filter dropdown. |
| `replace_strings` | `mods.py:229` | 19 | 5% | 0 | 🟠 не покрыт | Bulk find-and-replace in translation column. |
| `sync_duplicates` | `mods.py:251` | 14 | 7% | 5 | 🟠 не покрыт | Apply a translation to all strings with the same original text. |
| `mod_context` | `mods.py:268` | 6 | 17% | 0 | 🟡 частично | Redirect to SPA context editor (API is at /api/mods/<name>/context). |
| `_load_validation` | `mods.py:276` | 12 | 8% | 0 | 🔴 мёртвый | Load saved validation results for a mod. |
| `_load_nexus_cache` | `mods.py:290` | 15 | 7% | 0 | 🔴 мёртвый | Load cached Nexus data for a mod (individual {mod_id}.json file). Accepts a ModInfo object |
| `_run` | `ota_rt.py:20` | 10 | 10% | 36 | 🟠 не покрыт | Run a subprocess, return (returncode, combined output). |
| `host_commit` | `ota_rt.py:33` | 4 | 25% | 0 | 🟡 частично | Return the host's current git commit hash. |
| `status` | `ota_rt.py:40` | 23 | 4% | 961 | 🟠 не покрыт | Return current git state and how many commits behind origin/main. |
| `update` | `ota_rt.py:66` | 42 | 2% | 69 | 🟠 не покрыт | 1. git pull 2. npm run build (only if frontend files changed) 3. Schedule os.execv restart |
| `servers_page` | `servers_rt.py:16` | 10 | 10% | 0 | 🟠 не покрыт |  |
| `trigger_scan` | `servers_rt.py:29` | 28 | 4% | 2 | 🟠 не покрыт | Kick off a background LAN scan. Returns immediately. |
| `_sessions` | `single_rt.py:19` | 2 | 50% | 7 | 🟡 частично |  |
| `_get_session` | `single_rt.py:23` | 5 | 20% | 5 | 🟡 частично |  |
| `upload` | `single_rt.py:33` | 83 | 1% | 6 | 🟠 не покрыт | Accept a mod ZIP, extract it, parse ESP strings → SQLite, return session_id. |
| `get_strings` | `single_rt.py:121` | 22 | 4% | 9 | 🟠 не покрыт | Paginated strings — same shape as /mods/<name>/strings. |
| `update_string` | `single_rt.py:148` | 22 | 4% | 1 | 🟠 не покрыт |  |
| `translate_one` | `single_rt.py:175` | 170 | 1% | 9 | 🟠 не покрыт | Synchronously translate a single string via AI (pull-mode worker). |
| `translate_bulk` | `single_rt.py:350` | 54 | 2% | 0 | 🟠 не покрыт | Create a translate_strings job for this session's mod. |
| `download` | `single_rt.py:409` | 57 | 2% | 48 | 🟠 не покрыт | Apply translations to ESP copies, repack ZIP, stream to browser. |
| `delete_session` | `single_rt.py:471` | 17 | 6% | 4 | 🟠 не покрыт |  |
| `terms_page` | `terms_rt.py:12` | 4 | 25% | 0 | 🟡 частично |  |
| `save_terms` | `terms_rt.py:19` | 9 | 11% | 0 | 🟠 не покрыт |  |
| `add_term` | `terms_rt.py:31` | 11 | 9% | 0 | 🟠 не покрыт |  |
| `delete_term` | `terms_rt.py:45` | 7 | 14% | 0 | 🟡 частично |  |
| `_terms_path` | `terms_rt.py:54` | 5 | 20% | 3 | 🟡 частично |  |
| `_load_terms` | `terms_rt.py:61` | 8 | 12% | 5 | 🟡 частично |  |
| `_save` | `terms_rt.py:71` | 4 | 25% | 2 | 🟡 частично |  |
| `_within_allowed_roots` | `tools_rt.py:14` | 26 | 54% | 1 | 🟡 частично | True if `raw` resolves inside a configured root (mods / temp / backup / model-cache / proj |
| `_reject_path` | `tools_rt.py:42` | 6 | 83% | 7 | 🟢 покрыт | Return a 403 response if any request-supplied path escapes the allowed roots, else None. |
| `tools_page` | `tools_rt.py:51` | 4 | 25% | 0 | 🟡 частично |  |
| `esp_parse` | `tools_rt.py:60` | 24 | 46% | 0 | 🟡 частично | Parse an ESP file and return extracted strings as JSON. |
| `esp_validate` | `tools_rt.py:87` | 19 | 5% | 1 | 🟠 не покрыт | Validate translated ESP strings for token preservation, encoding, etc. |
| `esp_apply` | `tools_rt.py:109` | 11 | 9% | 1 | 🟠 не покрыт | Apply translation cache to ESP — write translated strings into binary. |
| `bsa_unpack` | `tools_rt.py:125` | 30 | 33% | 2 | 🟡 частично |  |
| `bsa_pack` | `tools_rt.py:158` | 28 | 4% | 2 | 🟠 не покрыт |  |
| `swf_decompile` | `tools_rt.py:191` | 27 | 4% | 2 | 🟠 не покрыт |  |
| `swf_list_fonts` | `tools_rt.py:221` | 21 | 5% | 0 | 🟠 не покрыт | List fonts embedded in a SWF file. |
| `swf_fix_fonts` | `tools_rt.py:245` | 52 | 2% | 0 | 🟠 не покрыт | Replace fonts in a SWF with a Cyrillic-capable TTF. |
| `swf_compile` | `tools_rt.py:300` | 25 | 4% | 2 | 🟠 не покрыт |  |
| `hash_manager` | `tools_rt.py:330` | 7 | 14% | 0 | 🟡 частично |  |
| `compute_hashes` | `tools_rt.py:340` | 6 | 17% | 0 | 🟡 частично | Compute hashes for all mod ESP/BSA files and return as JSON. |
| `_build_hash_list` | `tools_rt.py:348` | 22 | 4% | 2 | 🟠 не покрыт |  |
| `xtranslate_import` | `tools_rt.py:375` | 70 | 1% | 0 | 🟠 не покрыт | Import translations from an SST/xTranslate .t3dict file. SST format (text-based): [FormID] |
| `xtranslate_export` | `tools_rt.py:448` | 64 | 2% | 0 | 🟠 не покрыт | Export current translation cache as SST/xTranslate-compatible .t3dict text file. Format: [ |
| `nexus_fetch` | `tools_rt.py:515` | 32 | 3% | 0 | 🟠 не покрыт | Manually fetch Nexus description for a mod. |
| `safe_under` | `utils.py:9` | 13 | 62% | 10 | 🟢 покрыт | Join user-supplied path parts under `base` and confine the result to `base`. Aborts 400 on |
| `get_mod_path` | `utils.py:24` | 15 | 7% | 26 | 🟠 не покрыт | Return the absolute path to a mod folder, searching all configured mods_dirs. Uses the sca |

### Приложение и конфиг

| Функция / метод | Файл:строка | loc | Покрытие | Ссылок | Статус | Назначение |
|---|---|---|---|---|---|---|
| `_setup_logging` | `cli.py:15` | 23 | 0% | 7 | 🟠 не покрыт |  |
| `cli` | `cli.py:44` | 11 | 0% | 11 | 🟠 не покрыт | Nolvus Translator — automatic Skyrim mod localization pipeline. |
| `translate_esp` | `cli.py:67` | 10 | 0% | 2 | 🟠 не покрыт | Translate strings embedded in an ESP/ESM/ESL plugin. |
| `translate_mcm` | `cli.py:85` | 4 | 0% | 15 | 🟠 не покрыт | Translate MCM interface .txt files (loose and BSA-embedded). |
| `translate_mod` | `cli.py:97` | 22 | 0% | 22 | 🟠 не покрыт | Translate both ESP and MCM files for an entire mod folder. |
| `translate_all` | `cli.py:127` | 32 | 0% | 15 | 🟠 не покрыт | Translate all mods in the configured mods_dir. |
| `PathsConfig.mods_dir` | `config.py:35` | 3 | 67% | 114 | 🟢 покрыт | Primary (first) mods directory — for backward compatibility. |
| `_resolve` | `config.py:145` | 4 | 75% | 5 | 🟢 покрыт | Resolve a path relative to project root if not absolute. |
| `_model_cfg` | `config.py:151` | 19 | 16% | 3 | 🟡 частично |  |
| `load_config` | `config.py:172` | 113 | 26% | 34 | 🟡 частично |  |
| `get_config` | `config.py:287` | 5 | 80% | 27 | 🟢 покрыт | Return cached config, loading it if needed. |
| `create_app` | `app.py:17` | 595 | 24% | 7 | 🟡 частично |  |
| `save_translation` | `workers.py:12` | 56 | 2% | 18 | 🟠 не покрыт | Unified save dispatcher — routes to StringManager by key prefix. SQLite is the single sour |
| `translate_all_worker` | `workers.py:70` | 80 | 1% | 4 | 🟠 не покрыт | Translate all mods in mods_dir. scope: "all" \| "esp" \| "mcm" \| "bsa" \| "swf" \| "revie |
| `_translate_mod_filtered` | `workers.py:152` | 28 | 4% | 1 | 🟠 не покрыт | Helper: translate a mod using translate_strings_worker with filter options. Used by transl |
| `apply_mod_worker` | `workers.py:182` | 5 | 20% | 4 | 🟡 частично | Apply ESP translations from SQLite to ESP/ESM binaries. |
| `translate_bsa_worker` | `workers.py:190` | 5 | 20% | 3 | 🟡 частично | Apply BSA/MCM/SWF translations from SQLite → disk → repack. |
| `bsa_unpack_worker` | `workers.py:197` | 11 | 9% | 2 | 🟠 не покрыт | Unpack a BSA archive. |
| `bsa_pack_worker` | `workers.py:210` | 11 | 9% | 2 | 🟠 не покрыт | Pack a directory into BSA. |
| `swf_decompile_worker` | `workers.py:223` | 11 | 9% | 2 | 🟠 не покрыт | Decompile SWF using JPEXS Free Flash Decompiler (ffdec.jar). |
| `swf_compile_worker` | `workers.py:236` | 11 | 9% | 2 | 🟠 не покрыт | Recompile SWF from decompiled directory. |
| `validate_translations_worker` | `workers.py:249` | 4 | 25% | 4 | 🟡 частично | Validate translated strings — delegates to ValidatePipeline. |
| `recompute_scores_worker` | `workers.py:255` | 4 | 25% | 2 | 🟡 частично | Recompute quality scores — delegates to RecomputePipeline. |
| `translate_strings_worker` | `workers.py:261` | 47 | 2% | 18 | 🟠 не покрыт | Thin shim — delegates to TranslatePipeline. Legacy force=True maps to TranslationMode.FORC |
| `auto_translate_worker` | `workers.py:310` | 105 | 39% | 7 | 🟡 частично | VM2/VM3 — auto/variable-model phased translation. Plans the mod's pending strings into dif |

### Remote (host-side client)

| Функция / метод | Файл:строка | loc | Покрытие | Ссылок | Статус | Назначение |
|---|---|---|---|---|---|---|
| `TranslationClient.__init__` | `client.py:26` | 4 | 25% | 75 | 🟡 частично |  |
| `TranslationClient._get` | `client.py:33` | 8 | 12% | 6 | 🟡 частично |  |
| `TranslationClient._post` | `client.py:42` | 9 | 11% | 3 | 🟠 не покрыт |  |
| `TranslationClient.submit_translate` | `client.py:54` | 21 | 5% | 1 | 🟠 не покрыт | POST /translate → returns job_id. params: InferenceParams — forwarded so server uses same  |
| `TranslationClient.submit_infer` | `client.py:76` | 8 | 12% | 1 | 🟡 частично | POST /infer → returns job_id. prompt: pre-built ChatML string; params: InferenceParams (sa |
| `TranslationClient.submit_chat` | `client.py:85` | 4 | 25% | 2 | 🟡 частично | POST /chat → returns job_id. |
| `TranslationClient.poll_job` | `client.py:92` | 42 | 2% | 2 | 🟠 не покрыт | Poll GET /jobs/{job_id} until status is "done" or "error" (or timeout). Args: job_id: Job  |
| `TranslationClient.poll_job_liveness` | `client.py:135` | 65 | 2% | 4 | 🟠 не покрыт | Heartbeat-based polling: timeout resets as long as the server responds. Unlike poll_job(), |
| `TranslationClient.translate` | `client.py:203` | 22 | 4% | 205 | 🟠 не покрыт | Submit a translate job and block until complete. Returns list of translated strings (same  |
| `TranslationClient.infer` | `client.py:226` | 17 | 6% | 22 | 🟠 не покрыт | Submit a pre-built prompt for raw inference and block until complete. Returns the raw mode |
| `TranslationClient.chat` | `client.py:244` | 13 | 8% | 23 | 🟠 не покрыт | Submit a chat job and block until complete. Returns the assistant response string. Raises  |
| `TranslationClient.health` | `client.py:260` | 3 | 33% | 49 | 🟡 частично | GET /health → {"status": "ok", "model_loaded": bool, "queue_depth": int} |
| `TranslationClient.info` | `client.py:264` | 3 | 33% | 369 | 🟡 частично | GET /info → {"platform": str, "gpu": str, "model": str, "version": str} |
| `TranslationClient.get_stats` | `client.py:268` | 3 | 33% | 3 | 🟡 частично | GET /stats → aggregate performance stats. |
| `TranslationClient.get_jobs` | `client.py:272` | 3 | 33% | 0 | 🔴 мёртвый | GET /jobs → list of recent job dicts. |
| `TranslationClient.is_reachable` | `client.py:276` | 7 | 14% | 0 | 🔴 мёртвый | Non-raising connectivity check. |
| `TranslationClient.close` | `client.py:284` | 2 | 50% | 65 | 🟡 частично |  |
| `LanScanner.__init__` | `scanner.py:40` | 3 | 0% | 75 | 🟠 не покрыт |  |
| `LanScanner.scan` | `scanner.py:46` | 21 | 0% | 44 | 🟠 не покрыт | Full scan: mDNS first, then TCP fallback if nothing found. Returns deduplicated list of Se |
| `LanScanner.scan_mdns_only` | `scanner.py:68` | 47 | 0% | 2 | 🟠 не покрыт | Browse for _skylator._tcp.local. services. Requires: pip install zeroconf |
| `LanScanner.scan_tcp_only` | `scanner.py:116` | 41 | 0% | 2 | 🟠 не покрыт | Scan all 192.168.x.x/24 subnets for the configured port. Uses threading for speed (up to 2 |
| `LanScanner._get_local_subnets` | `scanner.py:160` | 14 | 0% | 1 | 🟠 не покрыт | Return list of /24 subnet prefixes like ["192.168.1", "192.168.0"]. |
| `LanScanner._fetch_info` | `scanner.py:175` | 10 | 0% | 1 | 🟠 не покрыт | Quick /info fetch to confirm and identify a server. |
| `JobRecord.__init__` | `server.py:69` | 17 | 0% | 75 | 🟠 не покрыт |  |
| `JobRecord.elapsed` | `server.py:87` | 5 | 0% | 78 | 🟠 не покрыт |  |
| `JobRecord.to_dict` | `server.py:93` | 17 | 0% | 36 | 🟠 не покрыт |  |
| `ServerState.__init__` | `server.py:115` | 12 | 0% | 75 | 🟠 не покрыт |  |
| `ServerState.tps_avg` | `server.py:129` | 4 | 0% | 73 | 🟠 не покрыт |  |
| `ServerState.tps_last` | `server.py:135` | 4 | 0% | 44 | 🟠 не покрыт |  |
| `ServerState.detect_gpu` | `server.py:140` | 32 | 0% | 3 | 🟠 не покрыт |  |
| `ServerState.add_job` | `server.py:173` | 2 | 0% | 7 | 🟠 не покрыт |  |
| `ServerState.finish_job` | `server.py:176` | 6 | 0% | 3 | 🟠 не покрыт | Move job to completed list; prune oldest if over 100. |
| `ServerState.notify_subscribers` | `server.py:183` | 7 | 0% | 7 | 🟠 не покрыт |  |
| `_register_mdns` | `server.py:200` | 33 | 0% | 3 | 🟠 не покрыт |  |
| `_unregister_mdns` | `server.py:235` | 10 | 0% | 3 | 🟠 не покрыт |  |
| `_worker` | `server.py:249` | 40 | 0% | 7 | 🟠 не покрыт | Single-worker loop: process one job at a time from state.queue. |
| `_run_translate` | `server.py:291` | 45 | 0% | 3 | 🟠 не покрыт |  |
| `_run_infer` | `server.py:338` | 31 | 0% | 6 | 🟠 не покрыт | Execute raw inference on a pre-built prompt. No prompt building on the server side. |
| `_run_chat` | `server.py:371` | 27 | 0% | 3 | 🟠 не покрыт |  |
| `_get_my_url` | `server.py:402` | 12 | 0% | 3 | 🟠 не покрыт | Determine this server's LAN URL to report to the host. |
| `_register_with_host` | `server.py:416` | 21 | 0% | 2 | 🟠 не покрыт | POST /api/workers/register to the Windows host. Returns True on success. |
| `_unregister_from_host` | `server.py:439` | 8 | 0% | 1 | 🟠 не покрыт |  |
| `_register_and_heartbeat` | `server.py:449` | 26 | 0% | 3 | 🟠 не покрыт | Background task: register on startup, then send heartbeats every 15 s. |
| `_pull_worker_loop` | `server.py:477` | 76 | 0% | 3 | 🟠 не покрыт | Pull-mode inference worker: polls host for chunks, infers, posts results. Eliminates the n |
| `create_server_app` | `server.py:557` | 200 | 0% | 6 | 🟠 не покрыт | Build the FastAPI application with async job queue. |

### Агент: сервер

| Функция / метод | Файл:строка | loc | Покрытие | Ссылок | Статус | Назначение |
|---|---|---|---|---|---|---|
| `_send` | `agent_link.py:47` | 2 | 100% | 4 | 🟢 покрыт |  |
| `AgentLink.__init__` | `agent_link.py:52` | 17 | 76% | 75 | 🟢 покрыт |  |
| `AgentLink._connect_once` | `agent_link.py:71` | 13 | 54% | 1 | 🟡 частично |  |
| `AgentLink.serve_forever` | `agent_link.py:85` | 17 | 76% | 4 | 🟢 покрыт | Dial the master and service pushed commands until stopped. Reconnects on drop. |
| `AgentLink._read_loop` | `agent_link.py:103` | 13 | 85% | 1 | 🟢 покрыт |  |
| `AgentLink._handle_command` | `agent_link.py:118` | 12 | 75% | 1 | 🟢 покрыт |  |
| `AgentLink.send` | `agent_link.py:132` | 10 | 70% | 12 | 🟢 покрыт |  |
| `AgentLink.send_telemetry` | `agent_link.py:143` | 4 | 50% | 2 | 🟡 частично | Event-driven telemetry — call when state changes (tps, current string, progress), not on a |
| `AgentLink.wait_connected` | `agent_link.py:148` | 2 | 100% | 2 | 🟢 покрыт |  |
| `AgentLink.connected` | `agent_link.py:152` | 2 | 50% | 29 | 🟡 частично |  |
| `AgentLink.has_listening_socket` | `agent_link.py:156` | 3 | 67% | 1 | 🟢 покрыт | Always False — the agent only ever dials out. There is no inbound surface. |
| `AgentLink.close` | `agent_link.py:160` | 7 | 71% | 65 | 🟢 покрыт |  |
| `AgentLink._close_sock` | `agent_link.py:168` | 7 | 71% | 2 | 🟢 покрыт |  |
| `_Cfg.__init__` | `config.py:45` | 2 | 0% | 75 | 🟠 не покрыт |  |
| `load_config` | `config.py:49` | 35 | 0% | 34 | 🟠 не покрыт | Load a server_config.yaml. Returns _Cfg with .ensemble.model_b and .ensemble.backend_type. |
| `_get_git_commit` | `remote_server.py:48` | 12 | 0% | 2 | 🟠 не покрыт | Return short git commit hash of this repo, or '' if not in a git repo. |
| `_download_staged_files` | `remote_server.py:64` | 44 | 0% | 1 | 🟠 не покрыт | Download staged model files from host and return updated payload with model_path. Called f |
| `JobRecord.__init__` | `remote_server.py:182` | 17 | 0% | 75 | 🟠 не покрыт |  |
| `JobRecord.elapsed` | `remote_server.py:200` | 4 | 0% | 78 | 🟠 не покрыт |  |
| `JobRecord.to_dict` | `remote_server.py:205` | 17 | 0% | 36 | 🟠 не покрыт |  |
| `_estimate_tokens` | `remote_server.py:226` | 9 | 0% | 2 | 🟠 не покрыт | [FIX #5] Estimate token count with Cyrillic-aware heuristic. Cyrillic text tokenises at ~2 |
| `_compute_recommended_params` | `remote_server.py:251` | 24 | 0% | 1 | 🟠 не покрыт | [FIX #6] Derive recommended inference params from measured TPS and hardware. |
| `ServerState.__init__` | `remote_server.py:280` | 28 | 0% | 75 | 🟠 не покрыт |  |
| `ServerState.tps_avg` | `remote_server.py:310` | 2 | 0% | 73 | 🟠 не покрыт |  |
| `ServerState.tps_last` | `remote_server.py:314` | 2 | 0% | 44 | 🟠 не покрыт |  |
| `ServerState.detect_gpu` | `remote_server.py:317` | 27 | 0% | 3 | 🟠 не покрыт |  |
| `ServerState.detect_capabilities` | `remote_server.py:345` | 13 | 0% | 2 | 🟠 не покрыт |  |
| `ServerState.detect_hardware` | `remote_server.py:359` | 76 | 0% | 1 | 🟠 не покрыт | Detect hardware — called once at startup. Caches static fields (CPU name/cores). Dynamic f |
| `ServerState.refresh_free_memory` | `remote_server.py:436` | 30 | 0% | 10 | 🟠 не покрыт | [FIX #8] Update only the free-memory fields — cheap, called on every heartbeat. |
| `ServerState.add_job` | `remote_server.py:467` | 2 | 0% | 7 | 🟠 не покрыт |  |
| `ServerState.finish_job` | `remote_server.py:470` | 4 | 0% | 3 | 🟠 не покрыт |  |
| `ServerState.notify_subscribers` | `remote_server.py:475` | 7 | 0% | 7 | 🟠 не покрыт |  |
| `_register_mdns` | `remote_server.py:494` | 29 | 0% | 3 | 🟠 не покрыт |  |
| `_unregister_mdns` | `remote_server.py:525` | 9 | 0% | 3 | 🟠 не покрыт |  |
| `_build_backend` | `remote_server.py:538` | 44 | 0% | 7 | 🟠 не покрыт | [FIX #7] Instantiate a backend — single ModelConfig construction path. |
| `_watch_download` | `remote_server.py:584` | 17 | 0% | 2 | 🟠 не покрыт | A5 — poll a model dir's on-disk bytes and publish download progress to state (surfaced via |
| `_model_download_dir` | `remote_server.py:603` | 14 | 0% | 2 | 🟠 не покрыт | The local dir a load/download will write into (for the progress watcher). None if nothing  |
| `_download_only` | `remote_server.py:619` | 13 | 0% | 4 | 🟠 не покрыт | A4 — fetch model files WITHOUT loading into VRAM (pre-provision a fleet). Returns the reso |
| `_worker` | `remote_server.py:636` | 33 | 0% | 7 | 🟠 не покрыт |  |
| `_run_translate` | `remote_server.py:671` | 36 | 0% | 3 | 🟠 не покрыт |  |
| `_run_infer` | `remote_server.py:709` | 21 | 0% | 6 | 🟠 не покрыт |  |
| `_run_chat` | `remote_server.py:732` | 12 | 0% | 3 | 🟠 не покрыт |  |
| `_record_tps` | `remote_server.py:746` | 16 | 0% | 3 | 🟠 не покрыт |  |
| `_get_my_url` | `remote_server.py:766` | 11 | 0% | 3 | 🟠 не покрыт |  |
| `_get_cached_models` | `remote_server.py:779` | 45 | 0% | 2 | 🟠 не покрыт | Return list of cached models (GGUF files + MLX dirs) in models_cache/. |
| `_post_result` | `remote_server.py:828` | 18 | 0% | 10 | 🟠 не покрыт | [FIX #2] Post chunk result to host with exponential-backoff retry. |
| `_async_register` | `remote_server.py:848` | 46 | 0% | 2 | 🟠 не покрыт | Register with host. Returns True on success. |
| `_socket_load_model` | `remote_server.py:896` | 19 | 0% | 1 | 🟠 не покрыт | Socket command handler: load/hot-swap a model. Mirrors the pull-loop load_model path (buil |
| `_socket_unload_model` | `remote_server.py:917` | 4 | 0% | 1 | 🟠 не покрыт |  |
| `_maybe_start_agent_link` | `remote_server.py:923` | 29 | 0% | 1 | 🟠 не покрыт | Start the persistent outbound socket to the master (once). The agent DIALS the master (NAT |
| `_apply_reconcile` | `remote_server.py:954` | 16 | 0% | 1 | 🟠 не покрыт | Act on the host's handshake verdict. 'resume'/'unknown' → keep working; 'reconciled' → hos |
| `_async_unregister` | `remote_server.py:972` | 6 | 0% | 1 | 🟠 не покрыт |  |
| `_register_and_heartbeat` | `remote_server.py:982` | 119 | 0% | 3 | 🟠 не покрыт |  |
| `_row_to_result` | `remote_server.py:1105` | 15 | 0% | 2 | 🟠 не покрыт | Map a ResultStore row to the /offline-results payload shape. |
| `_post_offline_results` | `remote_server.py:1122` | 19 | 0% | 2 | 🟠 не покрыт | POST a batch of durable results to the host. Returns (ok, confirmed_seq, failed_seqs). |
| `_deliver_loop` | `remote_server.py:1143` | 56 | 0% | 2 | 🟠 не покрыт | Always-on loop: push undelivered durable results to the host and mark them delivered on ac |
| `_watchdog_loop` | `remote_server.py:1201` | 42 | 0% | 1 | 🟠 не покрыт | Detect a hung/degraded model during offline production: if the durable result seq stops ad |
| `_produce_assignment` | `remote_server.py:1245` | 18 | 0% | 2 | 🟠 не покрыт | Run the store-driven runner for one assignment, then mark it complete. Safe to call on a f |
| `_run_offline_job` | `remote_server.py:1265` | 23 | 0% | 1 | 🟠 не покрыт | Persist the assignment + manifest durably, then produce. Delivery is handled by the always |
| `_pull_worker_loop` | `remote_server.py:1292` | 335 | 0% | 3 | 🟠 не покрыт | Poll host for work chunks → execute → post result back. [FIX #9] Uses async httpx directly |
| `create_server_app` | `remote_server.py:1631` | 366 | 0% | 6 | 🟠 не покрыт |  |
| `_setup_logging` | `server.py:37` | 12 | 0% | 7 | 🟠 не покрыт |  |
| `main` | `server.py:51` | 93 | 0% | 229 | 🟠 не покрыт |  |

### Агент: модели

| Функция / метод | Файл:строка | loc | Покрытие | Ссылок | Статус | Назначение |
|---|---|---|---|---|---|---|
| `BaseBackend.__init__` | `base.py:22` | 2 | 0% | 75 | 🟠 не покрыт |  |
| `BaseBackend.is_loaded` | `base.py:26` | 2 | 0% | 28 | 🟠 не покрыт |  |
| `BaseBackend.load` | `base.py:30` | 2 | 0% | 166 | 🟠 не покрыт | Load model weights into VRAM. |
| `BaseBackend.translate` | `base.py:34` | 7 | 0% | 205 | 🟠 не покрыт | Translate a list of strings. Returns a list of the same length. Never raises — returns ori |
| `BaseBackend.unload` | `base.py:42` | 10 | 0% | 31 | 🟠 не покрыт | Free GPU memory. Call between model swaps. |
| `BaseBackend._do_unload` | `base.py:53` | 2 | 0% | 8 | 🟠 не покрыт | Override in subclasses to release model references. |
| `BaseBackend.__enter__` | `base.py:56` | 3 | 0% | 1 | 🟠 не покрыт |  |
| `BaseBackend.__exit__` | `base.py:60` | 2 | 0% | 1 | 🟠 не покрыт |  |
| `InferenceParams.as_dict` | `inference_params.py:31` | 11 | 9% | 14 | 🟠 не покрыт |  |
| `InferenceParams.from_dict` | `inference_params.py:44` | 11 | 18% | 22 | 🟡 частично |  |
| `InferenceParams.defaults` | `inference_params.py:57` | 3 | 33% | 34 | 🟡 частично | Return params with all None — backend will use its ModelConfig defaults. |
| `get_token_stats` | `llamacpp_backend.py:32` | 2 | 0% | 7 | 🟠 не покрыт |  |
| `get_performance_stats` | `llamacpp_backend.py:36` | 13 | 0% | 7 | 🟠 не покрыт |  |
| `reset_token_stats` | `llamacpp_backend.py:51` | 7 | 0% | 3 | 🟠 не покрыт |  |
| `LlamaCppBackend.__init__` | `llamacpp_backend.py:63` | 4 | 0% | 75 | 🟠 не покрыт |  |
| `LlamaCppBackend.load` | `llamacpp_backend.py:68` | 26 | 0% | 166 | 🟠 не покрыт |  |
| `LlamaCppBackend._do_unload` | `llamacpp_backend.py:95` | 3 | 0% | 8 | 🟠 не покрыт |  |
| `LlamaCppBackend.translate` | `llamacpp_backend.py:99` | 36 | 0% | 205 | 🟠 не покрыт |  |
| `LlamaCppBackend._infer` | `llamacpp_backend.py:136` | 10 | 0% | 14 | 🟠 не покрыт | Raw inference on a pre-built prompt (pull-mode). stop_check: optional callable () -> bool, |
| `LlamaCppBackend._chat` | `llamacpp_backend.py:149` | 36 | 0% | 11 | 🟠 не покрыт |  |
| `LlamaCppBackend._translate_batch` | `llamacpp_backend.py:186` | 26 | 0% | 4 | 🟠 не покрыт |  |
| `_lock_hf_cache` | `loader.py:19` | 7 | 0% | 1 | 🟠 не покрыт | Force HuggingFace to use our local cache, not system dirs. |
| `resolve_gguf` | `loader.py:31` | 56 | 0% | 7 | 🟠 не покрыт | Return absolute path to a .gguf file. Resolution order: 1. local_dir_name is an absolute p |
| `_find_cached_snapshot` | `mlx_backend.py:15` | 39 | 0% | 6 | 🟠 не покрыт | Scan cache_dir for an existing MLX model snapshot — no network access. Searches in priorit |
| `MlxBackend.__init__` | `mlx_backend.py:59` | 9 | 0% | 75 | 🟠 не покрыт |  |
| `MlxBackend.load` | `mlx_backend.py:69` | 67 | 0% | 166 | 🟠 не покрыт |  |
| `MlxBackend._do_unload` | `mlx_backend.py:137` | 9 | 0% | 8 | 🟠 не покрыт |  |
| `MlxBackend.translate` | `mlx_backend.py:147` | 72 | 0% | 205 | 🟠 не покрыт |  |
| `MlxBackend._chat` | `mlx_backend.py:220` | 28 | 0% | 11 | 🟠 не покрыт | Raw chat inference — no translation prompt wrapping. Used by /chat endpoint. |
| `MlxBackend._infer` | `mlx_backend.py:249` | 43 | 0% | 14 | 🟠 не покрыт | Raw inference on a pre-built prompt (pull-mode). stop_check: optional callable () -> bool. |

### Агент: durability

| Функция / метод | Файл:строка | loc | Покрытие | Ссылок | Статус | Назначение |
|---|---|---|---|---|---|---|
| `_inline_quality_score` | `offline_translate.py:22` | 31 | 42% | 10 | 🟡 частично | Lightweight quality score (0-100) for use without the host esp_engine. Checks: - Cyrillic  |
| `OfflineTranslateRunner.__init__` | `offline_translate.py:83` | 7 | 100% | 75 | 🟢 покрыт |  |
| `OfflineTranslateRunner.cancel` | `offline_translate.py:91` | 2 | 100% | 39 | 🟢 покрыт |  |
| `OfflineTranslateRunner.run` | `offline_translate.py:94` | 144 | 42% | 190 | 🟡 частично | Produce translations for all pending manifest items, writing each durably. Retries inferen |
| `compute_hash` | `result_store.py:100` | 3 | 67% | 5 | 🟢 покрыт | SHA256[:32] of original text — MUST match the master's StringManager hash. |
| `ResultStore.__init__` | `result_store.py:108` | 10 | 100% | 75 | 🟢 покрыт |  |
| `ResultStore._init_schema` | `result_store.py:121` | 12 | 58% | 3 | 🟡 частично |  |
| `ResultStore.migrate` | `result_store.py:134` | 25 | 56% | 5 | 🟡 частично | Apply any pending agent-DB migrations in place. Idempotent — safe on every startup, includ |
| `ResultStore.disk_full` | `result_store.py:161` | 2 | 100% | 8 | 🟢 покрыт |  |
| `ResultStore.health` | `result_store.py:164` | 15 | 27% | 49 | 🟡 частично | Operational flags for the heartbeat (Phase 10): disk pressure, whether the agent has any o |
| `ResultStore.checkpoint` | `result_store.py:180` | 7 | 57% | 18 | 🟡 частично | Truncate the WAL so it does not grow without bound over a long run. |
| `ResultStore.close` | `result_store.py:188` | 6 | 67% | 65 | 🟢 покрыт |  |
| `ResultStore.add_assignment` | `result_store.py:197` | 20 | 30% | 10 | 🟡 частично | Persist a new assignment and its manifest. Idempotent (INSERT OR IGNORE). |
| `ResultStore.add_manifest_items` | `result_store.py:218` | 31 | 45% | 1 | 🟡 частично | Insert manifest rows. Each item: {string_id, string_hash?, original, mod_name?, esp_name?, |
| `ResultStore.pending_items` | `result_store.py:250` | 10 | 40% | 5 | 🟡 частично | Manifest rows not yet done — the agent's resume work list. |
| `ResultStore.open_assignments` | `result_store.py:261` | 6 | 67% | 15 | 🟢 покрыт |  |
| `ResultStore.get_assignment` | `result_store.py:268` | 6 | 17% | 19 | 🟡 частично |  |
| `ResultStore.set_assignment_state` | `result_store.py:275` | 7 | 57% | 5 | 🟡 частично |  |
| `ResultStore.assignment_progress` | `result_store.py:283` | 9 | 11% | 2 | 🟠 не покрыт | (total, done) for an assignment's manifest. |
| `ResultStore.write_result` | `result_store.py:295` | 44 | 20% | 7 | 🟡 частично | Durably record one produced translation and mark its manifest row done. Returns the new mo |
| `ResultStore.undelivered` | `result_store.py:342` | 8 | 50% | 41 | 🟡 частично | Rows not yet acked by the master (push path). |
| `ResultStore.results_since` | `result_store.py:351` | 8 | 50% | 12 | 🟡 частично | Rows with seq > since_seq (master-pull path). Read-only, safe anytime. |
| `ResultStore.mark_delivered_seqs` | `result_store.py:360` | 15 | 53% | 2 | 🟡 частично | Mark a specific set of seqs delivered. Lets the deliver loop ack every row the host saved  |
| `ResultStore.mark_delivered` | `result_store.py:376` | 10 | 60% | 6 | 🟢 покрыт | Mark all rows seq <= up_to_seq as delivered (master acked them). |
| `ResultStore.max_seq` | `result_store.py:387` | 4 | 100% | 35 | 🟢 покрыт |  |
| `ResultStore.mark_undelivered_since` | `result_store.py:392` | 12 | 50% | 2 | 🟡 частично | Re-arm results with seq > since_seq for delivery (set delivered=0). Used when the master a |
| `ResultStore.undelivered_count` | `result_store.py:405` | 12 | 42% | 5 | 🟡 частично |  |
| `ResultStore.all_assignments` | `result_store.py:418` | 4 | 25% | 1 | 🟡 частично |  |
| `ResultStore.get_meta` | `result_store.py:425` | 4 | 100% | 5 | 🟢 покрыт |  |
| `ResultStore.set_meta` | `result_store.py:430` | 8 | 12% | 1 | 🟡 частично |  |
| `ResultStore.is_done_sent` | `result_store.py:439` | 2 | 50% | 1 | 🟡 частично |  |
| `ResultStore.set_done_sent` | `result_store.py:442` | 2 | 50% | 1 | 🟡 частично |  |
| `ResultStore.prune_confirmed` | `result_store.py:445` | 17 | 71% | 7 | 🟢 покрыт | Delete delivered results the master has confirmed reconciled, keeping a safety margin. Onl |
| `ResultStore.digest` | `result_store.py:465` | 20 | 5% | 24 | 🟠 не покрыт | Compact state summary for the reconnect handshake with the master. |
---

## Что этот разбор показал

### 1. Мёртвый код — фичи, которых на самом деле нет

15 объектов не вызываются ниоткуда. Три из них — не мелочь, а заявленные функции, которые не подключены к работе:

| Что | Где | Чем это оборачивается |
|---|---|---|
| **Консенсус двух моделей** — `resolve_consensus` (55 строк) | `ensemble/consensus.py` | Модуль недостижим. В `config.yaml` есть секция `ensemble.consensus` с параметрами, она парсится в `ConsensusConfig` — и не влияет ни на что. То, что пайплайн помечает `source="consensus"`, это `pick_better` (сравнение нового перевода со старым) — совсем другая механика |
| **Контекст записи ESP** — `ContextBuilder.get_record_context` + весь `esp_context.py` (141 строка) | `context/` | FormID → (тип записи, EDID, группа) вычисляется, но в промпт не попадает. Модель не знает, переводит она название меча или реплику диалога |
| **Импорт legacy `.trans.json` при старте** — `start_background_import` | `db/importer.py` | Не запускается никогда |

Остальные 12 — мелкие осиротевшие хелперы: `build_tm_block`, `load_causal_lm`, `cmd_apply_from_trans`, `_load_nexus_cache`, `_load_validation`, `AssignmentStore.expected_hash`, `TranslationClient.get_jobs`/`is_reachable`, `SwfStringCache.get_text_files`, `TranslationDB.is_empty`, `ModelStateManager.get_desired`, `import_xtranslate_file`.

### 2. Категории без единого покрытого теста

| Категория | Функций | Покрыто | Комментарий |
|---|---|---|---|
| **Пайплайны перевода** | 18 | **0** | `translate_pipeline.py` (650 строк) — сердце приложения, тестов нет вообще |
| **Парсинг форматов** | 17 | **0** | BSA, MCM, SWF, ESP-обёртки — ни одного теста |
| **Ансамбль моделей** | 11 | **0** | из них 1 мёртвый, остальные недостижимы через мёртвый вход |
| **Агент: модели** | 30 | **0** | llama.cpp и MLX бэкенды агента |
| **Remote (host-side client)** | 45 | **0** | HTTP-клиент и LAN-сканер |
| **Сканер модов** | 22 | **0** | `mod_scanner.py`, 844 строки |

Это не значит «сломано»: пайплайн перевода и бэкенды агента я гонял вживую в этой сессии. Но у них нет ни одной автоматической страховки — любая правка в них ломается молча.

### 3. Где страховка есть

| Категория | Покрытие |
|---|---|
| Реестр воркеров и провод | 41 из 58 |
| Джобы и оркестрация | 47 из 88 |
| Агент: durability | 11 из 34, остальные частично |
| Движки файлов (scripts) | 38 из 85 |

Координация — самая проверенная часть системы, и это правильный приоритет: именно она отвечает за «не потерять работу».
