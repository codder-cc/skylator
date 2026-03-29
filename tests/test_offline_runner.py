"""
Tests for OfflineTranslateRunner and _inline_quality_score.

Covers:
- _inline_quality_score: 100 for correct Russian translation
- _inline_quality_score: penalty for identical output (untranslated)
- _inline_quality_score: penalty for no Cyrillic in multi-word string
- _inline_quality_score: penalty for missing tokens (<Alias=...>, %1)
- _inline_quality_score: 0 for empty translation
- _inline_quality_score: multiple penalties compound
- OfflineTranslateRunner.cancel(): sets _stop, runner exits early
- OfflineTranslateRunner.run(): delivers every DELIVER_EVERY strings (done=False)
- OfflineTranslateRunner.run(): delivers remaining strings at end (done=True)
- OfflineTranslateRunner.run(): empty string list → single done=True delivery
- OfflineTranslateRunner.run(): done_count tracks actual strings processed
- Full cycle: 120 strings → 2 intermediate deliveries + 1 final done=True
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from remote_worker.offline_translate import (
    DELIVER_EVERY,
    OfflineTranslateRunner,
    _inline_quality_score,
)


# ── _inline_quality_score ─────────────────────────────────────────────────


def test_score_good_russian_translation():
    score = _inline_quality_score("Hello world", "Привет мир")
    assert score == 100


def test_score_zero_for_empty_translation():
    assert _inline_quality_score("Hello", "") == 0


def test_score_penalty_for_untranslated():
    # Output == input → -40
    score = _inline_quality_score("Hello world", "Hello world")
    assert score <= 60


def test_score_penalty_for_no_cyrillic_multiword():
    # Multi-word string, output has no Cyrillic → -30
    score = _inline_quality_score("The quick brown fox", "The quick brown fox translated")
    # Still no Cyrillic in translation → penalty
    assert score < 100


def test_score_no_cyrillic_penalty_for_short_strings():
    # Short 1-2 word strings — numbers/codes don't need Cyrillic
    score = _inline_quality_score("FX01", "FX01")
    # Untranslated penalty (-40) but no Cyrillic penalty (only 1 word)
    assert score == 60


def test_score_penalty_for_missing_alias_token():
    # <Alias=Follower> must be preserved
    score = _inline_quality_score(
        "Talk to <Alias=Follower>",
        "Поговорите с кем-то",  # missing <Alias=Follower>
    )
    assert score <= 85


def test_score_penalty_for_missing_percent_token():
    score = _inline_quality_score("You have %1 gold", "У вас золото")  # missing %1
    assert score <= 85


def test_score_no_penalty_when_token_preserved():
    score = _inline_quality_score(
        "Talk to <Alias=Follower>",
        "Поговорите с <Alias=Follower>",
    )
    assert score == 100


def test_score_multiple_missing_tokens_compound():
    score = _inline_quality_score(
        "Give %1 to <Alias=Target> and %2 to <Alias=Other>",
        "Дайте кому-то что-то",  # all 4 tokens missing
    )
    # -15 per missing token × 4 = -60
    assert score <= 40


def test_score_nl_token_preserved():
    score = _inline_quality_score("Line1⟨NL⟩Line2", "Строка1⟨NL⟩Строка2")
    assert score == 100


def test_score_clamped_at_zero():
    # Many missing tokens can't push below 0
    score = _inline_quality_score(
        "<A> <B> <C> <D> <E> <F> <G> <H> <I> <J>",
        "",  # empty translation → 0
    )
    assert score == 0


# ── OfflineTranslateRunner ────────────────────────────────────────────────


def _make_job_data(n_strings=5, batch_size=4):
    strings = [
        {"id": i, "key": f"k{i}", "esp": "Mod.esp",
         "mod_name": "TestMod", "original": f"Text {i}"}
        for i in range(n_strings)
    ]
    return {
        "strings":         strings,
        "context":         "Test context",
        "src_lang":        "English",
        "tgt_lang":        "Russian",
        "params":          {"batch_size": batch_size, "temperature": 0.3},
        "terminology":     "",
        "preserve_tokens": [],
        "tm_pairs":        {},
        "offline_job_id":  "oj-test",
        "host_job_id":     "hj-test",
        "mod_name":        "TestMod",
    }


def _make_state(translations=None):
    """Return a fake ServerState whose backend._infer returns numbered translations."""
    state = MagicMock()
    call_count = [0]

    def _fake_infer(prompt, params=None):
        batch_size = 4
        # Parse how many items are in the prompt (rough heuristic for tests)
        lines = [l for l in prompt.split("\n") if l.strip().startswith(("1.", "2.", "3.", "4.", "5."))]
        n = len(lines) if lines else batch_size
        call_count[0] += 1
        if translations:
            batch_translations = translations[(call_count[0]-1)*batch_size: call_count[0]*batch_size]
            return "\n".join(f"{i+1}. {t}" for i, t in enumerate(batch_translations))
        return "\n".join(f"{i+1}. Перевод {call_count[0]}-{i}" for i in range(n))

    state.backend._infer = _fake_infer
    return state


def _run(runner, state, n_strings):
    """Run the OfflineTranslateRunner synchronously via asyncio.run()."""
    deliveries = []

    async def _deliver(results, done):
        deliveries.append({"results": results, "done": done})

    loop = asyncio.new_event_loop()
    try:
        with patch("remote_worker.offline_translate.OfflineTranslateRunner.run",
                   wraps=runner.run):
            # Patch the imports inside run() to avoid loading actual model
            with patch.dict("sys.modules", {
                "prompt.builder":          MagicMock(build_prompt=lambda **kw: "prompt"),
                "prompt.parser":           MagicMock(parse_numbered_output=_fake_parse),
                "models.inference_params": MagicMock(
                    InferenceParams=MagicMock(from_dict=lambda d: MagicMock())
                ),
            }):
                loop.run_until_complete(runner.run(state, loop, _deliver))
    finally:
        loop.close()
    return deliveries


def _fake_parse(raw, expected):
    """Minimal parse: return `expected` fake translations."""
    return [f"Перевод {i}" for i in range(expected)]


# ── Async test helpers ─────────────────────────────────────────────────────


async def _async_run(runner, state, n_strings):
    deliveries = []

    async def _deliver(results, done):
        deliveries.append({"results": results, "done": done})

    loop = asyncio.get_event_loop()

    with patch.dict("sys.modules", {
        "prompt.builder":          MagicMock(**{"build_prompt.return_value": "prompt"}),
        "prompt.parser":           MagicMock(**{"parse_numbered_output.side_effect": _fake_parse}),
        "models.inference_params": MagicMock(
            **{"InferenceParams.from_dict.return_value": MagicMock()}
        ),
    }):
        await runner.run(state, loop, _deliver)

    return deliveries


# ── Tests ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_runner_empty_strings():
    runner = OfflineTranslateRunner(_make_job_data(n_strings=0))
    state  = _make_state()
    deliveries = await _async_run(runner, state, 0)

    assert len(deliveries) == 1
    assert deliveries[0]["done"]    is True
    assert deliveries[0]["results"] == []


@pytest.mark.asyncio
async def test_runner_small_batch_single_delivery():
    """5 strings < DELIVER_EVERY → only one final delivery with done=True."""
    runner = OfflineTranslateRunner(_make_job_data(n_strings=5, batch_size=4))
    state  = _make_state()
    deliveries = await _async_run(runner, state, 5)

    assert deliveries[-1]["done"] is True
    # No intermediate deliveries since 5 < DELIVER_EVERY (50)
    intermediate = [d for d in deliveries if not d["done"]]
    assert intermediate == []


@pytest.mark.asyncio
async def test_runner_delivers_every_50_strings():
    """110 strings → 2 intermediate deliveries (at 50, 100) + 1 final."""
    n = 110
    runner = OfflineTranslateRunner(_make_job_data(n_strings=n, batch_size=10))
    state  = _make_state()
    deliveries = await _async_run(runner, state, n)

    intermediate = [d for d in deliveries if not d["done"]]
    final        = [d for d in deliveries if d["done"]]

    assert len(intermediate) == 2
    assert len(final)        == 1


@pytest.mark.asyncio
async def test_runner_done_count_equals_n_strings():
    n      = 15
    runner = OfflineTranslateRunner(_make_job_data(n_strings=n, batch_size=4))
    state  = _make_state()
    await _async_run(runner, state, n)
    assert runner.done_count == n


@pytest.mark.asyncio
async def test_runner_all_results_have_required_fields():
    n      = 4
    runner = OfflineTranslateRunner(_make_job_data(n_strings=n, batch_size=4))
    state  = _make_state()
    deliveries = await _async_run(runner, state, n)

    all_results = [r for d in deliveries for r in d["results"]]
    assert len(all_results) == n

    for r in all_results:
        assert "original"    in r
        assert "translation" in r
        assert "status"      in r
        assert "quality_score" in r
        assert 0 <= r["quality_score"] <= 100


@pytest.mark.asyncio
async def test_runner_cancel_stops_early():
    """cancel() sets _stop; runner exits after the current batch."""
    n      = 200
    runner = OfflineTranslateRunner(_make_job_data(n_strings=n, batch_size=10))
    state  = _make_state()

    call_count  = [0]
    deliveries  = []
    cancelled   = False

    async def _deliver(results, done):
        nonlocal cancelled
        deliveries.append({"results": results, "done": done})
        if not cancelled and call_count[0] == 0:
            runner.cancel()
            cancelled = True
        call_count[0] += 1

    loop = asyncio.get_event_loop()
    with patch.dict("sys.modules", {
        "prompt.builder":          MagicMock(**{"build_prompt.return_value": "p"}),
        "prompt.parser":           MagicMock(**{"parse_numbered_output.side_effect": _fake_parse}),
        "models.inference_params": MagicMock(
            **{"InferenceParams.from_dict.return_value": MagicMock()}
        ),
    }):
        await runner.run(state, loop, _deliver)

    # The runner was cancelled — done_count must be much less than n
    assert runner.done_count < n
    assert runner._stop is True
    # Final delivery must have done=True (runner always sends final)
    assert deliveries[-1]["done"] is True


@pytest.mark.asyncio
async def test_runner_full_cycle_120_strings():
    """
    Full lifecycle: 120 strings, batch_size=10.
    Expected: 2 intermediate flushes (at 50, 100 strings) + 1 final (20 remaining).
    """
    n      = 120
    runner = OfflineTranslateRunner(_make_job_data(n_strings=n, batch_size=10))
    state  = _make_state()
    deliveries = await _async_run(runner, state, n)

    done_flags        = [d["done"] for d in deliveries]
    intermediate_done = [d for d in deliveries if not d["done"]]
    final_done        = [d for d in deliveries if d["done"]]

    assert len(intermediate_done) == 2, (
        f"Expected 2 intermediate deliveries, got {len(intermediate_done)}"
    )
    assert len(final_done) == 1

    # Total results across all deliveries = 120
    total = sum(len(d["results"]) for d in deliveries)
    assert total == n

    # Each intermediate delivers exactly DELIVER_EVERY (50) results
    for d in intermediate_done:
        assert len(d["results"]) == DELIVER_EVERY

    # Final delivery has the remaining 20
    assert len(final_done[0]["results"]) == 20

    # done_count tracks all strings
    assert runner.done_count == n
