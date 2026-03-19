"""
Consensus logic: compare Model A vs Model B outputs,
send disagreements to arbiter (Model B in arbiter mode).
"""

from __future__ import annotations
import logging
from dataclasses import dataclass

from translator.ensemble.similarity import jaccard_similarity
from translator.config import get_config

log = logging.getLogger(__name__)


@dataclass
class ConsensusResult:
    translations:   list[str]
    agreed_count:   int
    arbitrated_count: int


def resolve_consensus(
    texts:        list[str],
    results_a:    list[str],
    results_b:    list[str],
    arbiter,                        # QwenBackend instance (already loaded)
    context:      str = "",
) -> ConsensusResult:
    """
    For each (a, b) pair:
      - If jaccard(a, b) >= threshold → use a (Model A is faster)
      - Otherwise → send to arbiter
    Returns ConsensusResult with final translations.
    """
    cfg = get_config().ensemble.consensus
    threshold    = cfg.similarity_threshold
    long_chars   = cfg.long_string_chars

    agreed_count   = 0
    need_arb_idx: list[int] = []

    final: list[str] = list(results_a)  # start with model A

    for i, (a, b, src) in enumerate(zip(results_a, results_b, texts)):
        sim = jaccard_similarity(a, b)
        log.debug(f"[{i}] sim={sim:.3f}  len={len(src)}")

        # Long strings always benefit from arbiter even if similar
        if sim >= threshold and len(src) <= long_chars:
            agreed_count += 1
        else:
            need_arb_idx.append(i)
            final[i] = b    # placeholder; will be replaced by arbiter

    arbitrated_count = len(need_arb_idx)

    if need_arb_idx and arbiter is not None:
        arb_texts = [texts[i]       for i in need_arb_idx]
        arb_a     = [results_a[i]   for i in need_arb_idx]
        arb_b     = [results_b[i]   for i in need_arb_idx]

        log.info(f"Sending {arbitrated_count}/{len(texts)} strings to arbiter...")
        arb_results = arbiter.arbitrate(arb_texts, arb_a, arb_b, context)

        for idx, arb_idx in enumerate(need_arb_idx):
            final[arb_idx] = arb_results[idx]

    log.info(
        f"Consensus: {agreed_count} agreed, {arbitrated_count} arbitrated "
        f"out of {len(texts)} total"
    )
    return ConsensusResult(
        translations=final,
        agreed_count=agreed_count,
        arbitrated_count=arbitrated_count,
    )
