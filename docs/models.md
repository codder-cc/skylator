# Translation Models

## Model A — HY-MT 1.5 7B GPTQ-Int4

| Property | Value |
|----------|-------|
| HuggingFace ID | `tencent/HY-MT1.5-7B-GPTQ-Int4` |
| Local dir | `D:/DevSpace/AI/HY-MT1.5-7B-GPTQ-Int4/` |
| VRAM | ~4.5 GB |
| Role | Primary translator (fast, MT specialist) |
| Context window | ~4096 tokens |
| Batch size | 20 strings |

HunyuanMT 1.5 — WMT25 competition champion for machine translation.
Specialized for sentence-level and paragraph-level translation.
Uses a simple instruction format (not ChatML):

```
Translate the following text from English to Russian: <text>
```

In our pipeline it gets a numbered-list prompt with context in a system block.

## Model B — Qwen2.5-14B-Instruct GPTQ-Int4

| Property | Value |
|----------|-------|
| HuggingFace ID | `Qwen/Qwen2.5-14B-Instruct-GPTQ-Int4` |
| Local dir | `D:/DevSpace/AI/Qwen2.5-14B-Instruct-GPTQ-Int4/` |
| VRAM | ~7–9 GB |
| Role | Secondary translator + consensus arbiter |
| Context window | 131 072 tokens |
| Batch size | 15 strings |

Qwen 2.5-14B is a general-purpose instruction-following LLM with large context.
Used for:
1. **Parallel translation** — second opinion on all strings
2. **Arbiter** — re-translates disagreements with both candidate translations
   shown as hints (`N. original | cand_A | cand_B`)

Uses ChatML format:
```
<|im_start|>system
...<|im_end|>
<|im_start|>user
...<|im_end|>
<|im_start|>assistant
```

---

## Current model (2026-03-21) — Qwen3.5-27B via llama-cpp-python

The GPTQ ensemble above was the original design.  The project switched to a single
large GGUF model via llama-cpp-python for better quality and simpler pipeline.

| Property | Value |
|----------|-------|
| HuggingFace ID | `Sepolian/Huihui-Qwen3.5-27B-Claude-4.6-Opus-abliterated-Q4_K_M` |
| Local path | `D:/DevSpace/AI/Huihui-Qwen3.5-27B-Claude-4.6-Opus-abliterated-Q4_K_M/` |
| Format | GGUF Q4_K_M (~15.8 GB) |
| VRAM | ~15.8 GB — fills the full 16 GB of the RTX 5080 |
| Backend | `translator/models/llamacpp_backend.py` (LlamaCppBackend) |
| Speed | ~11 tok/s (warm JIT cache), ~6.7s per dialog line (batch_size=4) |

### Architecture: hybrid SSM + attention (Qwen3.5 / qwen35)

64 layers total — 48 recurrent GDN (SSM/Mamba-style) + 16 full-attention.
This hybrid architecture has important implications:

- **CUDA graphs must be disabled** (`GGML_CUDA_DISABLE_GRAPHS=1`) — graph capture
  on Blackwell RTX 5080 with SSM layers causes 0.02 tok/s instead of 11 tok/s.
- **KV prefix reuse must be disabled** — the SSM recurrent state cannot be rewound
  to a previous position; the standard llama-cpp-python prefix optimization causes
  `llama_decode returned -1` on any two consecutive prompts that share a prefix.
- **Thinking must be disabled** — the Qwen3.5 chat template forces `<think>` on every
  assistant turn; without intervention the model spends 2000+ tokens reasoning before
  translating, consuming the entire token budget.

### Disabling thinking (Qwen3.5 chain-of-thought)

The model's Jinja2 chat template always starts assistant turns with `<|im_start|>assistant\n<think>\n`.
We bypass this by using `create_completion` (not `create_chat_completion`) with the raw
prompt and pre-filling `</think>\n\n` to immediately close the think block:

```python
prompt = (
    f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n"
    f"<|im_start|>user\n{user_prompt}<|im_end|>\n"
    f"<|im_start|>assistant\n</think>\n\n"        # ← skips chain-of-thought
)
resp = model.create_completion(
    prompt,
    stop=["<|im_end|>", "<|im_start|>"],
    echo=False,
    ...
)
text = resp["choices"][0]["text"].strip()         # NOT ["message"]["content"]
```

This is equivalent to Ollama's `think=False` option (Ollama: 65s / 15 lines; our
build: 100s / 15 lines — difference is batching overhead, not token speed).

### Build requirements

Custom llama-cpp-python build required — see **[llama_cpp_build.md](llama_cpp_build.md)**
and **[python_patches.md](python_patches.md)** for full details.

---

## Optional upgrade — Qwen2.5-32B

The 32B variant (`Qwen/Qwen2.5-32B-Instruct-GPTQ-Int4`) gives better quality
but requires ~16–18 GB VRAM.  Enable in `config.yaml`:

```yaml
ensemble:
  model_b:
    repo_id:        "Qwen/Qwen2.5-32B-Instruct-GPTQ-Int4"
    local_dir_name: "Qwen2.5-32B-Instruct-GPTQ-Int4"
    cpu_offload:    true
    max_memory:
      "cuda:0": "14GiB"
      cpu:      "28GiB"
```

With `cpu_offload: true` HuggingFace `accelerate` will spill overflow layers to
CPU RAM.  Performance is lower but quality is higher for long narrative strings.

## Neural Summarizer — BART-large-cnn

| Property | Value |
|----------|-------|
| HuggingFace ID | `facebook/bart-large-cnn` |
| Device | CPU (no GPU cost) |
| Role | Compress Nexus mod descriptions to ≤200 chars |

Runs only when `context.use_neural_summarizer: true` and the description is
longer than `context.summarize_threshold_chars` (default 400).
Falls back to simple truncation if the model fails to load.

## GPTQ loading (transformers ≥ 4.49)

`transformers>=4.49` loads GPTQ natively — no `auto-gptq` or `bitsandbytes`
needed at inference time.  The `optimum[gptq]` dependency is kept for
compatibility with older checkpoints that need it.

Load call:
```python
AutoModelForCausalLM.from_pretrained(path, torch_dtype=torch.float16, device_map="cuda:0")
```

## PyTorch for RTX 5080 (Blackwell sm_120)

RTX 5080 uses Blackwell architecture (compute capability 12.0).  Standard
PyTorch builds do not include sm_120 kernels — must install from the cu128
index:

```bat
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
```

This is handled automatically by `setup_venv.bat`.
