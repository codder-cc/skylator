# Skylator — HTTP API

Сгенерировано из кода на коммите `3810f70`. 157 маршрутов.

```

### translator/web/routes/api.py  (prefix: /api)
  GET                    /api/setup-reports                                       setup_reports                          
  POST                   /api/setup-reports/clear                                 clear_setup_reports                    
  GET                    /api/stats                                               stats                                  
  GET                    /api/mods                                                mods                                   
  GET                    /api/mods/by-id/<int:mod_id>                             mod_info_by_id                         Resolve a numeric mod ID to full ModInfo. Used by ID-based frontend routes.
  GET                    /api/mods/<path:mod_name>                                mod_info                               
  POST                   /api/mods/<path:mod_name>/reset-translations             reset_translations                     Reset all translatable ESP strings to pending (translation=null, status=pending, score=null).
  POST                   /api/mods/<path:mod_name>/fix-untranslatable             fix_untranslatable                     Set translation=original, score=100, status=translated for all untranslatable strings.
  GET                    /api/jobs                                                jobs                                   
  GET                    /api/jobs/<job_id>                                       job_detail                             
  GET                    /api/jobs/<job_id>/logs                                  job_logs                               
  GET                    /api/gpu                                                 gpu_info                               
  GET                    /api/nexus/test                                          nexus_test                             
  GET                    /api/mods/<path:mod_name>/context                        mod_context_api                        Return the AI context for a mod.
  GET                    /api/mods/<path:mod_name>/nexus                          mod_nexus_raw                          Return the raw Nexus mod description from disk cache (no API call).
  POST                   /api/mods/<path:mod_name>/nexus/fetch                    mod_nexus_fetch                        Synchronously fetch (or re-fetch) the raw Nexus description for a mod.
  POST                   /api/mods/<path:mod_name>/context                        save_mod_context                       Save custom context text for a mod.
  GET                    /api/tokens/stats                                        token_stats                            Return cumulative token usage across all translation calls this session.
  POST                   /api/tokens/reset                                        token_reset                            
  GET                    /api/mods/<path:mod_name>/validation                     mod_validation                         Return saved validation results for a mod.
  GET                    /api/models/status                                       models_status                          Return status of AI models (loaded / not loaded / downloading).
  GET                    /api/servers/test                                        servers_test                           Test a remote server — uses registry cache for pull-mode workers (no inbound connection).
  GET                    /api/servers                                             servers                                Return last known list of discovered LAN translation servers.
  POST                   /api/servers/scan                                        servers_scan                           Trigger a background LAN scan.
  GET                    /api/remote/config                                       remote_config_get                      Return current remote/local backend configuration and model info.
  GET                    /api/remote/stats                                        remote_stats                           Return stats for the configured remote server from registry cache (no inbound connection).
  GET                    /api/tokens/perf                                         tokens_perf                            Return merged performance stats from all inference sources.
  POST                   /api/mods/<path:mod_name>/strings/translate-one          translate_one_string                   Synchronously translate a single string via AI.
  GET                    /api/global-dict/stats                                   global_dict_stats                      Return global text dictionary statistics.
  POST                   /api/global-dict/rebuild                                 global_dict_rebuild                    Trigger a background rebuild of the global text dictionary.
  POST                   /api/global-dict/toggle                                  global_dict_toggle                     Enable or disable use_global_dict in config.yaml.
  GET                    /api/workers                                             workers_list                           Return all registered remote workers (active + recently seen).
  GET                    /api/workers/stream                                      workers_stream                         SSE stream of worker/agent state — pushed on every change (registry pub/sub), so the
  POST                   /api/workers/register                                    workers_register                       Remote server calls this on startup to announce itself.
  POST                   /api/workers/heartbeat                                   workers_heartbeat                      Remote server calls this every ~15 s to stay alive in the registry.
  DELETE                 /api/workers/<label>                                     workers_unregister                     Remote server calls this on clean shutdown.
  POST                   /api/workers/<label>/cancel-offline                      workers_cancel_offline                 Directly cancel an offline job on a worker without needing the host job.
  GET                    /api/workers/<label>/chunk                               workers_get_chunk                      Pull-mode: remote polls for next inference chunk.
  POST                   /api/workers/<label>/result                              workers_post_result                    Pull-mode: remote posts completed inference result.
  GET                    /api/assignments                                         assignments_overview                   Observability ledger (Phase 9): per-assignment funnel + agent liveness tier, plus
  GET                    /api/models/catalog                                      models_catalog                         Curated model catalog + per-default-ctx memory estimates (A3). Pass ?vram_mb= to get
  GET                    /api/models/estimate                                     models_estimate                        VRAM/KV estimate + fit + max_n_ctx (A2). Either pass ?catalog_id=&n_ctx=&vram_mb=
  GET                    /api/review/queue                                        review_queue                           G11 — needs_review strings ACROSS THE WHOLE PACK, worst-quality first. Optional
  POST                   /api/review/approve                                      review_approve                         G11 — batch-approve needs_review strings by id (cross-mod), flipping them to
  GET                    /api/mods/priorities                                     mods_priorities                        G9 — {folder_name: priority} for all mods (UI sort/badges).
  POST                   /api/mods/<path:name>/priority                           set_mod_priority                       G9 — set a mod's translation priority (higher = translated first by translate_all).
  POST                   /api/mods/<name>/import-translations                     import_translations                    A — seed a mod's strings from an existing community translation (xTranslate/SST XML).
  GET                    /api/mods/<name>/terminology-check                       terminology_check                      C — glossary consistency for a mod: translated strings whose original contains a
  GET                    /api/ledger/stats                                        ledger_stats                           #6 — projection read from the work ledger: fleet-wide done count, unique source texts,
  GET                    /api/translate/plan                                      translate_plan                         VM1 — preview the auto/variable-model plan for a mod's pending strings under a quality
  GET                    /api/campaign/estimate                                   campaign_estimate                      G8 — estimate time to finish the pending backlog across the live fleet's combined TPS.
  POST                   /api/models/dispatch                                     models_dispatch                        A4 — fan out a model download/load to several agents at once (non-blocking).
  GET                    /api/auto-feed                                           auto_feed_status                       Autonomous backlog draining status + how many strings remain unassigned.
  POST                   /api/auto-feed/start                                     auto_feed_start                        Turn on autonomous top-up: idle workers are continuously fed the next batch from
  POST                   /api/auto-feed/stop                                      auto_feed_stop                         
  POST                   /api/admin/rebuild-from-agents                           rebuild_from_agents                    Recovery (Gap 5): after restoring an older master DB backup, reset all agent pull
  POST                   /api/workers/<label>/abandon                             worker_abandon                         Operator action (Phase 7): immediately orphan an agent's active assignments
  POST                   /api/workers/<label>/offline-results                     workers_offline_results                Remote posts incremental/final results from an offline translate job.
  POST                   /api/workers/<label>/benchmark                           workers_benchmark                      Run a performance benchmark on a registered worker.
  POST                   /api/workers/<label>/ota-step                            workers_ota_step                       Worker POSTs each OTA step in real-time as it completes.
  POST                   /api/workers/<label>/ota-update                          workers_ota_update                     Trigger OTA update on a remote pull-mode worker.
  GET                    /api/model-transfer/file                                 model_transfer_file                    Stream a staged model file to the remote worker.
  POST                   /api/workers/<label>/model/load                          workers_model_load                     Download + load a model on a worker (see _do_model_load).
  POST                   /api/workers/<label>/model/download                      workers_model_download                 A4 — download/stage a model on a worker WITHOUT loading it into VRAM (pre-provision).
  POST                   /api/workers/<label>/model/unload                        workers_model_unload                   Send an unload_model command via the pull queue.
  GET                    /api/workers/<label>/info                                workers_get_info                       Return worker info from the registry (pushed via heartbeat).
  GET                    /api/workers/<label>/models                              workers_list_models                    Return cached .gguf files for a remote worker.
  GET                    /api/workers/model-defaults                              workers_model_defaults                 Durable per-agent default models (auto-restored when an agent comes up empty).
  POST,DELETE            /api/workers/<label>/model/default                       workers_model_default                  POST — set/re-arm an agent's default model without loading it now (body = model
  POST                   /api/remote/config                                       remote_config_set                      Save remote.mode and remote.server_url to config.yaml and reload config.
  GET                    /api/checkpoints                                         list_checkpoints                       
  POST                   /api/checkpoints/create                                  create_checkpoint                      
  POST                   /api/checkpoints/<checkpoint_id>/restore                 restore_checkpoint                     
  DELETE                 /api/checkpoints/<checkpoint_id>                         delete_checkpoint                      
  GET                    /api/strings/<int:string_id>/history                     get_string_history                     Return per-string translation history.
  POST                   /api/strings/<int:string_id>/approve                     approve_string                         Promote a needs_review string to translated.
  POST                   /api/mods/<path:mod_name>/strings/approve-bulk           approve_bulk_strings                   Approve multiple needs_review strings at once.
  GET                    /api/mods/<path:mod_name>/strings/conflicts              get_string_conflicts                   Return strings where the same original has 2+ different translations in this mod.
  POST                   /api/mods/<path:mod_name>/strings/resolve-conflict       resolve_conflict                       Set all strings with a given original to a single chosen translation.
  GET                    /api/stats/mods                                          get_all_mod_stats                      Return materialized stats for all mods from mod_stats_cache.
  POST                   /api/stats/recompute                                     recompute_stats                        Trigger a stats recompute for one mod or all mods.
  GET                    /api/mods/<path:mod_name>/reservations                   get_mod_reservations                   Return the strings currently in-flight for a mod — now from the live dispatch pool

### translator/web/routes/backups.py  (prefix: /backups)
  GET                    /backups/                                                    backup_list                            
  GET                    /backups/list                                                backup_list_json                       JSON version of backup list for React SPA.
  POST                   /backups/create                                              create_backup                          Create a backup of mod files.
  POST                   /backups/<path:backup_id>/restore                            restore_backup                         Restore a mod backup to its original location.
  POST                   /backups/<path:backup_id>/delete                             delete_backup                          
  POST                   /backups/restore-mod-esp                                     restore_mod_esp                        Restore all translatable file backups (ESP, ESM, BSA, SWF) for a mod and clear caches.
  POST                   /backups/trans-json/snapshot                                 snapshot_trans_json                    Save a lightweight snapshot of a mod's .trans.json files (just their current state).
  GET                    /backups/trans-json/list                                     list_trans_snapshots                   List all .trans.json snapshots for a mod.
  GET                    /backups/checkpoints                                         list_checkpoints                       
  POST                   /backups/checkpoints/create                                  create_checkpoint                      
  POST                   /backups/checkpoints/<checkpoint_id>/restore                 restore_checkpoint                     
  POST                   /backups/checkpoints/<checkpoint_id>/delete                  delete_checkpoint                      

### translator/web/routes/config_rt.py  (prefix: /config)
  GET                    /config/                                                    config_page                            
  POST                   /config/save                                                save_config                            
  GET                    /config/raw                                                 raw_config                             
  POST                   /config/validate                                            validate_config                        

### translator/web/routes/dashboard.py  (prefix: /)
  GET                    /                                                    index                                  

### translator/web/routes/jobs.py  (prefix: /jobs)
  GET                    /jobs/                                                    job_list                               
  GET                    /jobs/<job_id>                                            job_detail                             
  GET                    /jobs/<job_id>/stream                                     job_stream                             Server-Sent Events stream for a single job.
  GET                    /jobs/stream-all                                          stream_all                             SSE stream for all job updates.
  POST                   /jobs/create                                              create_job                             POST /jobs/create — create a new translation job.
  POST                   /jobs/<job_id>/cancel                                     cancel_job                             
  POST                   /jobs/<job_id>/retry                                      retry_job                              Re-create an identical job from a failed/cancelled job's stored params.
  POST                   /jobs/<job_id>/pause                                      pause_job                              Pause a running job — sets status=PAUSED, which triggers should_stop() in WorkerPool.
  POST                   /jobs/<job_id>/assign                                     assign_workers                         Assign workers to a job. Auto-resumes if job is paused.
  POST                   /jobs/<job_id>/unassign                                   unassign_workers                       Unassign workers from a job. Auto-pauses if no workers remain and job is running.
  POST                   /jobs/<job_id>/resume                                     resume_job                             Create a new job that continues where a paused/failed/cancelled translate job left off.
  POST                   /jobs/<job_id>/dispatch-back                              dispatch_back                          Cancel the offline job on all assigned workers.
  POST                   /jobs/<job_id>/dispatch-offline                           dispatch_offline_from_job              Pause a running translate job and dispatch remaining pending strings
  GET                    /jobs/<job_id>/tally                                      job_tally                              Live funnel for a job: how much was assigned, delivered, translated, pending.
  POST                   /jobs/<job_id>/collect                                    collect_job                            Deploy whatever is done — apply all translated strings for this job's mods to
  GET                    /jobs/<job_id>/export                                     export_job                             B2 — pull the DONE translations of this job as JSON (without deploying to ESP), so
  POST                   /jobs/clear                                               clear_finished                         

### translator/web/routes/logs_rt.py  (prefix: /logs)
  GET                    /logs/                                                    logs_page                              
  GET                    /logs/stream                                              stream_logs                            SSE stream — tail the log file in real time.
  GET                    /logs/tail                                                tail_logs                              Return last N lines of the log file as JSON (for initial load in React SPA).

### translator/web/routes/mods.py  (prefix: /mods)
  GET                    /mods/                                                    mod_list                               
  GET                    /mods/<path:mod_name>/strings                             mod_strings                            
  POST                   /mods/<path:mod_name>/strings/update                      update_string                          Update a single translation in the cache (ESP) or russian txt (MCM).
  GET                    /mods/<path:mod_name>/rec_types                           get_rec_types                          Return distinct record types for the record-type filter dropdown.
  POST                   /mods/<path:mod_name>/strings/replace                     replace_strings                        Bulk find-and-replace in translation column.
  POST                   /mods/<path:mod_name>/strings/sync-duplicates             sync_duplicates                        Apply a translation to all strings with the same original text.
  GET                    /mods/<path:mod_name>/context                             mod_context                            Redirect to SPA context editor (API is at /api/mods/<name>/context).

### translator/web/routes/ota_rt.py  (prefix: /api/ota)
  GET                    /api/ota/host-commit                                         host_commit                            Return the host's current git commit hash.
  GET                    /api/ota/status                                              status                                 Return current git state and how many commits behind origin/main.
  POST                   /api/ota/update                                              update                                 

### translator/web/routes/servers_rt.py  (prefix: /servers)
  GET                    /servers/                                                    servers_page                           
  POST                   /servers/scan                                                trigger_scan                           Kick off a background LAN scan. Returns immediately.

### translator/web/routes/single_rt.py  (prefix: /api/single-mod)
  POST                   /api/single-mod/upload                                              upload                                 Accept a mod ZIP, extract it, parse ESP strings → SQLite, return session_id.
  GET                    /api/single-mod/<session_id>/strings                                get_strings                            Paginated strings — same shape as /mods/<name>/strings.
  POST                   /api/single-mod/<session_id>/strings/update                         update_string                          
  POST                   /api/single-mod/<session_id>/strings/translate-one                  translate_one                          Synchronously translate a single string via AI (pull-mode worker).
  POST                   /api/single-mod/<session_id>/translate                              translate_bulk                         Create a translate_strings job for this session's mod.
  GET                    /api/single-mod/<session_id>/download                               download                               Apply translations to ESP copies, repack ZIP, stream to browser.
  DELETE                 /api/single-mod/<session_id>                                        delete_session                         

### translator/web/routes/terms_rt.py  (prefix: /terminology)
  GET                    /terminology/                                                    terms_page                             
  POST                   /terminology/save                                                save_terms                             
  POST                   /terminology/add                                                 add_term                               
  POST                   /terminology/delete                                              delete_term                            

### translator/web/routes/tools_rt.py  (prefix: /tools)
  GET                    /tools/                                                    tools_page                             
  POST                   /tools/esp/parse                                           esp_parse                              Parse an ESP file and return extracted strings as JSON.
  POST                   /tools/esp/validate                                        esp_validate                           Validate translated ESP strings for token preservation, encoding, etc.
  POST                   /tools/esp/apply                                           esp_apply                              Apply translation cache to ESP — write translated strings into binary.
  POST                   /tools/bsa/unpack                                          bsa_unpack                             
  POST                   /tools/bsa/pack                                            bsa_pack                               
  POST                   /tools/swf/decompile                                       swf_decompile                          
  POST                   /tools/swf/list-fonts                                      swf_list_fonts                         List fonts embedded in a SWF file.
  POST                   /tools/swf/fix-fonts                                       swf_fix_fonts                          Replace fonts in a SWF with a Cyrillic-capable TTF.
  POST                   /tools/swf/compile                                         swf_compile                            
  GET                    /tools/hashes                                              hash_manager                           
  POST                   /tools/hashes/compute                                      compute_hashes                         Compute hashes for all mod ESP/BSA files and return as JSON.
  POST                   /tools/xtranslate/import                                   xtranslate_import                      
  POST                   /tools/xtranslate/export                                   xtranslate_export                      
  POST                   /tools/nexus/fetch                                         nexus_fetch                            Manually fetch Nexus description for a mod.
```
