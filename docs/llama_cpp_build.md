# llama-cpp-python Build Guide (RTX 5080 / Blackwell)

## Problem

RTX 5080 uses Blackwell architecture (compute capability 12.0, `sm_120`).
Compiling llama-cpp-python natively for `sm_120` hits nvcc compiler bugs that cause
CUDA graph capture to malfunction on recurrent/SSM models → **0.02–0.04 tok/s** instead
of 11+ tok/s.

## Solution: compile for sm_89, JIT to sm_120

Compiling for Ada Lovelace (`sm_89`) generates PTX bytecode that CUDA's JIT compiler
translates to Blackwell SASS at first use.  The compiled SASS is cached in
`%USERPROFILE%\.nv\ComputeCache` — subsequent runs skip JIT and go full speed.

## Source location

```
C:/Users/<username>/AppData/Local/Temp/llama-cpp-python-src/
```

All Python patches are applied **to the source** so they survive every `pip` reinstall.

## Build command

```bat
cd C:\Users\<username>\AppData\Local\Temp\llama-cpp-python-src
set CMAKE_ARGS=-DGGML_CUDA=on -DCMAKE_CUDA_ARCHITECTURES=89
pip install . --upgrade --force-reinstall
```

- Do **not** use `--no-build-isolation` (breaks `scikit_build_core`).
- Build takes ~30–45 minutes (CUDA compilation).

## JIT warm-up

After a fresh build the first 2–3 inference calls are slow (0.1–0.5 tok/s) while
CUDA JIT-compiles kernels.  Run a dummy inference or just start the server and let
the first batch warm up.  After that: **~11 tok/s**.

---

## CMake patches applied to source

### 1. MACHO_CURRENT_VERSION on Windows (CMake 4.2.3)

**File:** `vendor/llama.cpp/tools/mtmd/CMakeLists.txt`

**Problem:** `MACHO_CURRENT_VERSION` is only valid on Apple. Also `VERSION`/`SOVERSION`
fail when `LLAMA_INSTALL_VERSION` is empty — the variable is defined in the
`vendor/llama.cpp` child scope and is not visible when `mtmd` is added from the
llama-cpp-python parent `CMakeLists.txt`.

**Fix:**
```cmake
if(NOT WIN32 AND LLAMA_INSTALL_VERSION)
    set_target_properties(mtmd PROPERTIES
        VERSION ${LLAMA_INSTALL_VERSION}
        SOVERSION 0
    )
endif()
if(APPLE AND LLAMA_INSTALL_VERSION)
    set_target_properties(mtmd PROPERTIES
        MACHO_CURRENT_VERSION 0
    )
endif()
```

### 2. Non-static `getenv` for CUDA graph disable

**File:** `vendor/llama.cpp/ggml/src/ggml-cuda/common.cuh`

**Problem:** `ggml_cuda_graph::is_enabled()` originally used `static const bool` to cache
`getenv("GGML_CUDA_DISABLE_GRAPHS")`.  On Windows, Python's `os.environ` calls Win32
`SetEnvironmentVariable()`, but MSVC's CRT `getenv()` has its own separate cache — so the
env var set by Python is invisible to the C runtime after the first check.

**Fix:** Remove `static` so the env var is re-read every call:
```cpp
bool is_enabled() const {
    // Not static — lets Python os.environ control this before first inference.
    const bool disable_cuda_graphs_due_to_env =
        (getenv("GGML_CUDA_DISABLE_GRAPHS") != nullptr);
    return !(disable_due_to_gpu_arch || disable_cuda_graphs_due_to_env);
}
```

> ⚠️ Do **not** use `GetEnvironmentVariableA` — it requires `<windows.h>` which is
> unavailable in CUDA compilation context.

---

## Python patches applied to source

All patches live in `C:/Users/<username>/AppData/Local/Temp/llama-cpp-python-src/llama_cpp/`.
See **[python_patches.md](python_patches.md)** for full details.

### Quick verification after rebuild

```bash
grep -n "except AttributeError" venv/Lib/site-packages/llama_cpp/_ctypes_extensions.py
grep -n "flash_attn_type\|samplers.*c_void\|n_samplers" venv/Lib/site-packages/llama_cpp/llama_cpp.py
grep -n "flash_attn_type\|kv_cache_clear\|_has_ssm" venv/Lib/site-packages/llama_cpp/llama.py
```

All three lines must return matches.  If any are missing the source patches were not
applied — recheck the source directory.

---

## Why CUDA graphs must be disabled

Qwen3.5 is a hybrid SSM+attention model.  CUDA graph capture on Blackwell with recurrent
layers causes 0.1 tok/s vs 11 tok/s with graphs off.

Set **before** any `llama_cpp` import:

```python
import os
os.environ.setdefault("GGML_CUDA_DISABLE_GRAPHS", "1")
# ... lazy import inside load():
from llama_cpp import Llama
```

In the project this is at the top of `translator/models/llamacpp_backend.py`.

---

## Performance reference

| Scenario | Speed |
|----------|-------|
| Cold JIT (first run after build) | 0.1–0.5 tok/s |
| Warm JIT (after cache populated) | ~11 tok/s |
| Ollama qwen3.5:27b (reference) | ~6–7 tok/s |
| CUDA graphs enabled (broken) | 0.02–0.04 tok/s |
