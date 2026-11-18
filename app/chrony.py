"""Read chrony remotely with `chronyc -c` and parse its CSV output."""
from __future__ import annotations

import math
import subprocess
from typing import List, Optional


class ChronyError(Exception):
    pass


def run_chronyc(cfg, *args: str, timeout: float = 8.0) -> str:
    # Keep the option list minimal: not every chronyc build accepts every flag.
    cmd = [cfg.chronyc_bin, "-n", "-c", "-h", cfg.chrony_host]
    if cfg.chrony_port != 323:
        cmd += ["-p", str(cfg.chrony_port)]
    cmd += list(args)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise ChronyError(f"{cfg.chronyc_bin} not found")
    except subprocess.TimeoutExpired:
        raise ChronyError(f"timeout talking to chrony at {cfg.chrony_host}:{cfg.chrony_port}")
    if p.returncode != 0:
        msg = (p.stdout.strip() or p.stderr.strip() or f"exit {p.returncode}")
        raise ChronyError(msg.splitlines()[0])
    return p.stdout


def _f(x: str) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else v


def _lines(text: str) -> List[List[str]]:
    return [ln.split(",") for ln in text.strip().splitlines() if ln.strip()]


def parse_tracking(text: str) -> dict:
    rows = _lines(text)
    if not rows or len(rows[0]) < 14:
        raise ChronyError("unexpected output from 'chronyc tracking'")
    f = rows[0]
    return {
        "ref_id": f[0].strip().upper(), "ref_name": f[1].strip(),
        "stratum": int(float(f[2])), "ref_time": _f(f[3]),
        "system_correction_s": _f(f[4]), "last_offset_s": _f(f[5]),
        "rms_offset_s": _f(f[6]), "freq_ppm": _f(f[7]),
        "resid_freq_ppm": _f(f[8]), "skew_ppm": _f(f[9]),
        "root_delay_s": _f(f[10]), "root_dispersion_s": _f(f[11]),
        "update_interval_s": _f(f[12]), "leap_status": f[13].strip(),
    }


def _octal(x: str) -> int:
    x = x.strip()
    try:
        return int(x, 8)
    except ValueError:
        return int(float(x))


def parse_sources(text: str) -> List[dict]:
    out = []
    for f in _lines(text):
        if len(f) < 10:
            continue
        out.append({
            "mode": f[0].strip(), "state": f[1].strip(), "name": f[2].strip(),
            "stratum": int(float(f[3])), "poll": int(float(f[4])),
            "reach": _octal(f[5]), "last_rx_s": _f(f[6]),
            "offset_s": _f(f[7]), "measured_s": _f(f[8]), "error_s": _f(f[9]),
        })
    if text.strip() and not out:
        raise ChronyError("unexpected output from 'chronyc sources'")
    return out


def parse_sourcestats(text: str) -> List[dict]:
    out = []
    for f in _lines(text):
        if len(f) < 8:
            continue
        out.append({
            "name": f[0].strip(), "np": int(float(f[1])), "nr": int(float(f[2])),
            "span_s": _f(f[3]), "freq_ppm": _f(f[4]), "freq_skew_ppm": _f(f[5]),
            "est_offset_s": _f(f[6]), "std_dev_s": _f(f[7]),
        })
    return out


_SERVERSTATS = ["ntp_rx", "ntp_dropped", "cmd_rx", "cmd_dropped", "log_dropped",
                "nts_ke_accepted", "nts_ke_dropped", "nts_auth", "interleaved",
                "ts_held", "ts_span"]


def parse_serverstats(text: str) -> dict:
    rows = _lines(text)
    if not rows or len(rows[0]) < 4:
        raise ChronyError("unexpected output from 'chronyc serverstats'")
    out = {}
    for key, val in zip(_SERVERSTATS, rows[0]):
        try:
            out[key] = int(float(val))
        except ValueError:
            pass
    return out


def merge_sources(sources: List[dict], stats: List[dict]) -> List[dict]:
    by_name = {s["name"]: s for s in stats}
    merged = []
    for s in sources:
        row = dict(s)
        st = by_name.get(s["name"], {})
        for k in ("np", "nr", "span_s", "freq_ppm", "freq_skew_ppm", "est_offset_s", "std_dev_s"):
            row[k] = st.get(k)
        merged.append(row)
    return merged
