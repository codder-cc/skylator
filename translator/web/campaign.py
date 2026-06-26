"""
Campaign-level ETA / cost estimate (G8).

Answers "how long will translating all the pending strings take across the current fleet?"
so a week-long run can be planned up front. Deliberately approximate (labelled ~).
"""
from __future__ import annotations


def _fmt_duration(seconds: float) -> str:
    s = int(max(0, seconds))
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    if h < 48:
        return f"{h}h {m}m"
    d, h = divmod(h, 24)
    return f"{d}d {h}h"


def estimate_campaign(pending: int, avg_chars: float, fleet_tps: float) -> dict:
    """Estimate wall-clock to translate `pending` strings of mean length `avg_chars` across a
    fleet doing `fleet_tps` tokens/sec combined.

    Token model (rough): input ≈ chars/3.5; Russian output ≈ 1.2× input; + ~20 tokens/string
    of prompt overhead. eta = total_tokens / fleet_tps.
    """
    if pending <= 0:
        return {"pending": 0, "eta_seconds": 0, "eta_human": "nothing pending", "approx": True}
    tok_in  = max(avg_chars, 1.0) / 3.5
    tok_per_string = tok_in + tok_in * 1.2 + 20.0          # input + output + overhead
    total_tokens   = pending * tok_per_string
    tps = max(fleet_tps, 0.1)
    eta = total_tokens / tps
    return {
        "pending": pending,
        "avg_chars": round(avg_chars, 1),
        "fleet_tps": round(tps, 1),
        "tokens_per_string_est": round(tok_per_string, 1),
        "total_tokens_est": round(total_tokens),
        "eta_seconds": round(eta),
        "eta_human": _fmt_duration(eta),
        "approx": True,
    }
