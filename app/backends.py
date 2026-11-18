"""Readers for the NTP daemon itself. Each returns the same normalised snapshot:

    {"tracking": {...}, "sources": [...], "serverstats": {...} | None}
"""
from __future__ import annotations

from typing import Callable, Dict, Optional

from . import ntpq
from .chrony import (ChronyError, merge_sources, parse_serverstats, parse_sources,
                     parse_sourcestats, parse_tracking, run_chronyc)

CHRONY_LOCAL_REF_ID = "7F7F0101"
_LEAP_NAMES = {0: "Normal", 1: "Insert second", 2: "Delete second", 3: "Not synchronised"}

# Exceptions that mean "this backend isn't available", as opposed to a bug.
BACKEND_ERRORS = (ChronyError, ntpq.NtpqError, ValueError, OSError)


def normalize_chrony_tracking(tr: dict) -> dict:
    out = dict(tr)
    out["jitter_s"] = None
    out["synced"] = tr["ref_id"] != "00000000" and tr["leap_status"].lower() != "not synchronised"
    out["local_clock"] = tr["ref_id"] == CHRONY_LOCAL_REF_ID
    return out


def chrony_snapshot(cfg) -> dict:
    tracking = normalize_chrony_tracking(parse_tracking(run_chronyc(cfg, "tracking")))
    sources = parse_sources(run_chronyc(cfg, "sources"))
    stats = parse_sourcestats(run_chronyc(cfg, "sourcestats"))
    merged = merge_sources(sources, stats)
    for s in merged:
        s.setdefault("delay_s", None)
    try:
        serverstats: Optional[dict] = parse_serverstats(run_chronyc(cfg, "serverstats"))
    except (ChronyError, ValueError):
        serverstats = None  # chrony only answers this one to local programs
    return {"tracking": tracking, "sources": merged, "serverstats": serverstats}


def ntpd_snapshot(cfg) -> dict:
    return ntpq.snapshot(cfg.server_host, cfg.ntp_port, timeout=2.0)


def probe_tracking(probe: dict) -> Optional[dict]:
    """Build a minimal 'tracking' from what any NTP server tells every client in its replies."""
    sv = probe.get("server")
    if not sv:
        return None
    name = (sv.get("ref_id") or "").strip(".")
    leap = sv.get("leap", 0)
    return {
        "ref_id": sv.get("ref_id") or "", "ref_name": name, "stratum": sv["stratum"], "ref_time": None,
        "system_correction_s": None, "last_offset_s": None, "rms_offset_s": None, "jitter_s": None,
        "freq_ppm": None, "resid_freq_ppm": None, "skew_ppm": None,
        "root_delay_s": sv.get("root_delay_s"), "root_dispersion_s": sv.get("root_dispersion_s"),
        "update_interval_s": None, "leap_status": _LEAP_NAMES.get(leap, "Not synchronised"),
        "synced": bool(sv.get("synced")), "local_clock": name.upper() in ("LOCL", "LOCAL"),
    }


FETCHERS: Dict[str, Callable[[object], dict]] = {"chrony": chrony_snapshot, "ntpd": ntpd_snapshot}
