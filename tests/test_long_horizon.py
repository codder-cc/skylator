"""
Phase 10 — long-horizon hardening: protocol/schema versioning + agent health flags.
"""
import sys
import tempfile
from pathlib import Path

_RW = Path(__file__).parent.parent / "remote_worker"
if str(_RW) not in sys.path:
    sys.path.insert(0, str(_RW))
from result_store import ResultStore, PROTOCOL_VERSION, SCHEMA_VERSION   # noqa: E402
from translator.jobs.assignment_store import PROTOCOL_VERSION as HOST_PROTOCOL  # noqa: E402


def test_protocol_versions_agree():
    # Agent and master must speak the same wire protocol by default.
    assert PROTOCOL_VERSION == HOST_PROTOCOL


def test_agent_migrate_is_idempotent():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "w.db"
        s = ResultStore(p)
        s.migrate(); s.migrate()                 # repeated calls must be safe
        assert s.get_meta("schema_version") == str(SCHEMA_VERSION)
        s.close()
        # Reopening (as on a restart / post-OTA) also migrates cleanly.
        s2 = ResultStore(p)
        assert s2.get_meta("schema_version") == str(SCHEMA_VERSION)
        s2.close()


def test_agent_migration_runner_applies_real_step():
    """Exercise the agent migration runner with an actual ALTER (Gap 6 coverage)."""
    import result_store as rs_mod
    saved = list(rs_mod._AGENT_MIGRATIONS)
    rs_mod._AGENT_MIGRATIONS.append((2, ["ALTER TABLE agent_results ADD COLUMN extra TEXT"]))
    try:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "w.db"
            s = ResultStore(p)   # __init__ runs migrate()
            cols = {r[1] for r in s._conn.execute("PRAGMA table_info(agent_results)").fetchall()}
            assert "extra" in cols
            assert s.get_meta("schema_version") == "2"
            s.migrate()          # idempotent — no error, version unchanged
            assert s.get_meta("schema_version") == "2"
            s.close()
    finally:
        rs_mod._AGENT_MIGRATIONS[:] = saved


def test_health_flags():
    with tempfile.TemporaryDirectory() as d:
        s = ResultStore(Path(d) / "w.db")
        h = s.health()
        assert h["open_assignments"] == 0 and h["undelivered"] == 0
        assert h["protocol"] == PROTOCOL_VERSION
        assert h["disk_full"] is False

        s.add_assignment("a1", items=[{"string_id": 1, "original": "Hello"}])
        assert s.health()["open_assignments"] == 1
        s.write_result("a1", 1, "Hello", "Привет", 95, "translated")
        assert s.health()["undelivered"] == 1
        s.close()
