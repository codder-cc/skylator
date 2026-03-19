# llama-cpp-python Python Patches

All patches must be applied to **both** locations:

| Location | Path |
|----------|------|
| Source (survives rebuild) | `C:/Users/<username>/AppData/Local/Temp/llama-cpp-python-src/llama_cpp/` |
| Installed (active) | `<project_root>/venv/Lib/site-packages/llama_cpp/` |

`pip install --upgrade --force-reinstall` installs FROM source, so patching source is
sufficient for future rebuilds.

---

## Patch 1 — `_ctypes_extensions.py`: graceful missing function handling

**Why:** The new DLL removes deprecated functions (`llama_get_kv_self`, `llama_kv_self_*`,
etc.).  Without this patch, import raises `AttributeError` and the whole library fails to
load.

In `ctypes_function` → `decorator`, wrap `getattr(lib, name)` in `try/except AttributeError`:

```python
def decorator(f: F) -> F:
    if enabled:
        try:
            func = getattr(lib, name)
            func.argtypes = argtypes
            func.restype = restype
            functools.wraps(f)(func)
            return func
        except AttributeError:
            import warnings
            warnings.warn(
                f"llama_cpp: C function '{name}' not found in shared library — "
                "API version mismatch between Python bindings and compiled DLL. "
                "Calls to this function will raise NotImplementedError.",
                stacklevel=4,
            )
            def _missing(*args, **kwargs):
                raise NotImplementedError(
                    f"llama_cpp: C function '{name}' is not available in this build."
                )
            return _missing
    else:
        return f
```

---

## Patch 2 — `llama_cpp.py`: `llama_context_params` struct fixes

**Why:** The ctypes struct must exactly match the C struct in the DLL.  Mismatch causes
wrong values written to wrong fields (symptom: `?` output, corrupted context).

Changes to `llama_context_params`:

1. Add `("flash_attn_type", ctypes.c_int)` **before** `rope_freq_base` (replaces the old
   `("flash_attn", ctypes.c_bool)` which must be removed from the booleans block).
2. Add after `kv_unified`:
   ```python
   ("samplers", ctypes.c_void_p),
   ("n_samplers", ctypes.c_size_t),
   ```

Correct C struct field order (from `llama.h`):
```
enum llama_flash_attn_type flash_attn_type   ← c_int, BEFORE rope_freq_base
float rope_freq_base
...booleans: embeddings, offload_kqv, no_perf, op_offload, swa_full, kv_unified
struct llama_sampler_seq_config * samplers   ← c_void_p
size_t n_samplers                            ← c_size_t
```

---

## Patch 3 — `llama.py`: flash_attn_type (enum not bool)

**Why:** API changed — `flash_attn` is now an enum value, not a bool.

Line ~344 (inside `__init__`):
```python
# OLD: self.context_params.flash_attn = flash_attn
self.context_params.flash_attn_type = 1 if flash_attn else 0
```

Line ~2099 (inside `__getstate__`):
```python
# OLD: flash_attn=self.context_params.flash_attn,
flash_attn=self.context_params.flash_attn_type != 0,
```

---

## Patch 4 — `llama.py`: KV cache API — old → new (embed path)

**Why:** `llama_kv_self_clear` was removed from the DLL; stubs silently do nothing,
causing the context to fill up and crash on subsequent calls.

Two locations inside the `embed()` method (lines ~1044 and ~1115):
```python
# OLD (no-op stub):
llama_cpp.llama_kv_self_clear(self._ctx.ctx)
# NEW:
self._ctx.kv_cache_clear()
```

---

## Patch 5 — `llama.py`: `reset()` must clear recurrent memory

**Why:** For hybrid SSM+attention models (Qwen3.5), `reset()` only zeroed `n_tokens`.
The recurrent (SSM) memory state was left intact, causing decode failures when the next
call started from an inconsistent state.

```python
def reset(self):
    """Reset the model state."""
    self.n_tokens = 0
    if hasattr(self, "_ctx") and self._ctx is not None:
        self._ctx.kv_cache_clear()
```

`kv_cache_clear()` calls `llama_memory_clear(memory, True)` which clears both the
recurrent SSM state and triggers KV cache trimming on the next `eval()` call.

---

## Patch 6 — `llama.py`: disable KV prefix reuse for SSM models

**Why:** `generate()` has an optimization that reuses the KV cache when consecutive prompts
share a prefix — it skips `reset()` and sets `n_tokens = prefix_length`.  This works for
pure-attention models but **breaks recurrent/SSM models**: the SSM state cannot be rewound
to a previous sequence position.  Symptom: `llama_decode returned -1` on the 2nd call
when prompts share any prefix tokens (e.g. "The capital of **France**" → "The capital of
**Germany**" shares "The capital of").

The error from llama.cpp:
```
init: the tokens of sequence 0 in the input batch have inconsistent sequence positions:
 - the last position stored in the memory module (i.e. the KV cache) for seq 0 is X = 13
 - the tokens for sequence 0 in the input batch have a starting position of Y = 3
```

Fix — detect SSM architecture from model metadata and skip prefix reuse:

```python
# Check for kv cache prefix match
# Disabled for recurrent/SSM hybrid models (e.g. Qwen3.5): the SSM
# state cannot be rewound to a previous position, so reusing the prefix
# causes decode failures on subsequent calls.
_arch = self.metadata.get("general.architecture", "")
_has_ssm = any(k.startswith(_arch + ".ssm") for k in self.metadata)
if reset and self.n_tokens > 0 and not _has_ssm:
    # ... existing prefix match logic unchanged ...
```

---

## Verification

```bash
grep -n "except AttributeError" venv/Lib/site-packages/llama_cpp/_ctypes_extensions.py
grep -n "flash_attn_type\|samplers.*c_void\|n_samplers" venv/Lib/site-packages/llama_cpp/llama_cpp.py
grep -n "flash_attn_type\|kv_cache_clear\|_has_ssm" venv/Lib/site-packages/llama_cpp/llama.py
```
