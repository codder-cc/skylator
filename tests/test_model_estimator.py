"""
A2/A3 — model memory/context estimator + curated catalog.
"""
from translator.web.model_estimator import (
    estimate_kv_cache_mb, estimate_total_vram_mb, max_n_ctx_for_vram, fit, estimate,
)
from translator.web.model_catalog import CATALOG, get_entry, enrich, catalog


def test_kv_cache_scales_with_ctx():
    kv_8k  = estimate_kv_cache_mb(8192, 48, 8, 128)
    kv_16k = estimate_kv_cache_mb(16384, 48, 8, 128)
    assert kv_16k == 2 * kv_8k          # linear in n_ctx
    assert kv_8k > 0


def test_qwen35_27b_total_is_in_the_right_ballpark():
    # 27B Q4_K_M ≈ 16 GB weights; at 8k ctx total should land near ~16.5–17.5 GB.
    b = estimate_total_vram_mb(weights_mb=16000, n_ctx=8192, n_layers=48, n_kv_heads=8, head_dim=128)
    assert b["weights_mb"] == 16000
    assert 16000 < b["total_mb"] < 18500     # weights + KV + overhead, within ~10%


def test_fit_classification():
    # 27B on 16 GB → no full fit (weights+overhead already near the limit) → tight or no.
    e16 = estimate(16000, 8192, 48, 8, 128, vram_mb=16384)
    assert e16["fit"] in ("tight", "no")
    # 14B (~9 GB) on a 24 GB card → comfortable.
    e24 = estimate(9000, 16384, 48, 8, 128, vram_mb=24576)
    assert e24["fit"] == "full" and e24["headroom_mb"] > 0


def test_max_n_ctx_for_vram_monotonic():
    small = max_n_ctx_for_vram(16384, 16000, 48, 8, 128)
    big   = max_n_ctx_for_vram(24576, 16000, 48, 8, 128)
    assert big > small                  # more VRAM → larger feasible context
    assert small % 512 == 0             # rounded to a token boundary


def test_weights_dont_fit_returns_no_and_zero_ctx():
    e = estimate(16000, 8192, 48, 8, 128, vram_mb=8192)   # 8 GB can't hold 16 GB weights
    assert e["fit"] == "no"
    assert max_n_ctx_for_vram(8192, 16000, 48, 8, 128) == 0


def test_catalog_entries_and_enrich():
    assert len(CATALOG) >= 3
    e = get_entry("qwen35-27b-q4km")
    assert e and e["backend"] == "llamacpp"
    enriched = enrich(e, vram_mb=24576)
    assert enriched["estimate"]["fit"] in ("full", "tight", "no")
    assert enriched["estimate"]["approx"] is True

    full = catalog(vram_mb=16384)
    assert all("estimate" in m for m in full)
    assert any(m["backend"] == "mlx" for m in full)   # MLX option present
