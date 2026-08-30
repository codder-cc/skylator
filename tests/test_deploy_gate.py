"""
ApplyPipeline._should_apply — the gate in front of writing into the game files.

Deploying is the irreversible step: it edits plugins in the mod folder. This gate
is what stands between "translate everything" and "ship half-finished text into
someone's playthrough", and it had no coverage.

DeployMode: all / skip_untranslated / skip_partial / skip_issues.
"""
from dataclasses import dataclass

import pytest

from translator.pipeline.apply_pipeline import ApplyPipeline
from translator.pipeline.translate_pipeline import DeployMode


@dataclass
class _Stats:
    total: int
    translated: int
    pending: int = 0
    needs_review: int = 0


class _StatsMgr:
    def __init__(self, stats): self._s = stats
    def get_mod_stats(self, mod_name): return self._s


class _Raising:
    def get_mod_stats(self, mod_name): raise RuntimeError("stats backend down")


class _Job:
    def __init__(self): self.logs = []
    def add_log(self, m): self.logs.append(m)


def _gate(stats_mgr, mode, stats=None):
    p = ApplyPipeline(cfg=None, repo=None, stats_mgr=stats_mgr)
    return p._should_apply("Mod", mode, _Job())


def test_all_deploys_everything():
    gate = _gate(_StatsMgr(_Stats(total=10, translated=0, pending=10)), DeployMode.ALL)
    assert gate is True


def test_skip_untranslated_blocks_a_mod_with_nothing_done():
    assert _gate(_StatsMgr(_Stats(total=10, translated=0, pending=10)),
                 DeployMode.SKIP_UNTRANSLATED) is False


def test_skip_untranslated_allows_any_progress():
    assert _gate(_StatsMgr(_Stats(total=10, translated=1, pending=9)),
                 DeployMode.SKIP_UNTRANSLATED) is True


def test_skip_partial_blocks_incomplete():
    assert _gate(_StatsMgr(_Stats(total=10, translated=9, pending=1)),
                 DeployMode.SKIP_PARTIAL) is False


def test_skip_partial_allows_complete():
    assert _gate(_StatsMgr(_Stats(total=10, translated=10)),
                 DeployMode.SKIP_PARTIAL) is True


def test_skip_issues_blocks_a_review_backlog():
    assert _gate(_StatsMgr(_Stats(total=10, translated=10, needs_review=3)),
                 DeployMode.SKIP_ISSUES) is False


def test_skip_issues_allows_a_clean_mod():
    assert _gate(_StatsMgr(_Stats(total=10, translated=10, needs_review=0)),
                 DeployMode.SKIP_ISSUES) is True


def test_no_stats_backend_does_not_block_deployment():
    """Without stats we cannot judge — refusing to deploy would be worse than allowing it."""
    assert _gate(None, DeployMode.SKIP_PARTIAL) is True


def test_stats_failure_does_not_block_deployment():
    p = ApplyPipeline(cfg=None, repo=None, stats_mgr=_Raising())
    assert p._should_apply("Mod", DeployMode.SKIP_PARTIAL, _Job()) is True


def test_a_skip_is_explained_in_the_job_log():
    """A silent skip looks identical to a successful deploy in the UI."""
    p = ApplyPipeline(cfg=None, repo=None,
                      stats_mgr=_StatsMgr(_Stats(total=10, translated=2, pending=8)))
    job = _Job()
    p._should_apply("Mod", DeployMode.SKIP_PARTIAL, job)
    assert any("Skip Mod" in l and "partial" in l for l in job.logs)
