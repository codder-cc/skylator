# Architecture

## Package layout

```
translator/
├── config.py               TypedConfig dataclasses + YAML singleton loader
├── pipeline.py             Public API shim (translate_batch, get_mod_context)
├── cli.py                  Click CLI entry point (nolvus-translate)
│
├── models/
│   ├── base.py             BaseBackend ABC + ModelState enum + unload/gc logic
│   ├── loader.py           resolve() path strategy + load_causal_lm() GPTQ helper
│   ├── hymt_backend.py     HY-MT 1.5 7B GPTQ-Int4 (Model A)
│   └── qwen_backend.py     Qwen2.5-14B GPTQ-Int4 (Model B) + arbitrate()
│
├── ensemble/
│   ├── pipeline.py         EnsemblePipeline — sequential A→unload→B→consensus
│   ├── consensus.py        Per-string similarity check + arbiter dispatch
│   └── similarity.py       Jaccard char-bigram on Cyrillic tokens
│
├── context/
│   ├── nexus_fetcher.py    Nexus Mods API + disk cache (TTL days)
│   ├── summarizer.py       BART summarizer (CPU) or truncation fallback
│   ├── esp_context.py      Lightweight ESP scanner → FormID→(type,EDID,group)
│   └── builder.py          ContextBuilder — combines mod description + record hint
│
└── prompt/
    ├── builder.py          Prompt templates for HY-MT, Qwen, and arbiter
    └── parser.py           Numbered list output parser

scripts/
├── esp_engine.py           ESP binary parser, string extractor, rewriter, CLI
└── translate_mcm.py        MCM .txt translator (loose + BSA), BSArch wrapper

data/
└── skyrim_terms.json       100+ EN→RU Skyrim terminology overrides
```

## Translation flow

```
translate_batch(texts, context)
        │
        ▼
EnsemblePipeline.translate()
        │
        ├─── 1. Load Model A (HY-MT)
        │         translate(texts) → results_a
        │         unload (free 4.5 GB VRAM)
        │
        ├─── 2. Load Model B (Qwen)
        │         translate(texts) → results_b
        │         (stay loaded for arbiter)
        │
        └─── 3. Consensus
                  for each (src, a, b):
                    jaccard(a, b) >= 0.82 AND len(src) <= 250
                        → use a  (agreed, Model A wins)
                    else
                        → send to arbiter
                  arbiter = Model B.arbitrate(texts, a_list, b_list)
                  unload Model B
```

## Context flow

```
get_mod_context(mod_folder)
        │
        ├─ NexusFetcher reads meta.ini → mod_id
        ├─ fetches api.nexusmods.com → raw description (HTML stripped)
        ├─ NeuralSummarizer (BART-large-cnn on CPU) → ≤200 char summary
        └─ cached on disk under cache/nexus_cache/<mod_id>.json

per-string context:
        EspContextExtractor scans ESP → EDID map
        ContextBuilder.build(mod_desc, record_ctx) → "Mod: ... | Record: [NPC_] EDID:..."
        → injected into prompt
```

## Model path resolution (loader.resolve)

```
1. D:/DevSpace/AI/<local_dir_name>/     ← pre-downloaded (fastest)
2. ~/.cache/huggingface/hub/.../snapshots/<hash>/   ← HF hub cache
3. snapshot_download(repo_id) → D:/DevSpace/AI/<local_dir_name>/  ← first run
```

## VRAM budget (RTX 5080, 16 GB)

| Model | Size (Int4) | Notes |
|-------|-------------|-------|
| HY-MT 1.5 7B | ~4.5 GB | Model A — loaded first, unloaded before B |
| Qwen2.5-14B | ~7–9 GB | Model B — loaded after A unloads |
| BART-large-cnn | CPU only | Summarizer, no GPU cost |

Sequential strategy: A and B are never in VRAM simultaneously.
KV-cache headroom after loading B: ~6–8 GB — sufficient for batch_size=15.

## ESP binary format notes

See [esp_format.md](esp_format.md) for full record / GRUP / VMAD details.
