"""Ties the NTP daemon, gpsd, the NTP probe and public reference servers together
and builds the dashboard status. Works for GPS/PPS servers and for plain network-synced ones."""
from __future__ import annotations

import logging
import os
import statistics
import threading
import time
from typing import Dict, List, Optional

from . import __version__
from .backends import BACKEND_ERRORS, FETCHERS, probe_tracking
from .gpsd import GpsdClient
from .ntpprobe import NtpProber, split_hostport
from .sats import normalize_sat, sub_satellite_point
from .store import Store

log = logging.getLogger("monitor")

METRICS = (
    "offset.last", "offset.rms", "offset.jitter", "pps.offset", "pps.stddev", "max_error",
    "root.dispersion", "freq.ppm", "freq.skew", "ref.stratum", "ntp.rate", "ntp.drop_rate",
    "gps.seen", "gps.used", "gps.hdop", "gps.fix",
    "probe.rtt", "probe.jitter", "probe.offset", "probe.loss", "sources.usable",
)
REF_PREFIX = "ref.diff."


def find_refclock(sources: List[dict]) -> Optional[dict]:
    """The reference clock (GPS, PPS, ...) to report on: the selected one, else a PPS, else any."""
    refs = [s for s in sources if s["mode"] == "#"]
    for s in refs:
        if s["state"] == "*":
            return s
    for s in refs:
        if "PPS" in s["name"].upper():
            return s
    return refs[0] if refs else None


def usable_upstream(sources: List[dict]) -> List[dict]:
    return [s for s in sources if s["mode"] in ("^", "=") and s["reach"] > 0
            and s["state"] in ("*", "+", "-")]


def max_error_s(tr: Optional[dict]) -> Optional[float]:
    if not tr or tr.get("root_dispersion_s") is None:
        return None
    return tr["root_dispersion_s"] + (tr.get("root_delay_s") or 0.0) / 2.0


def fmt_s(s: float) -> str:
    a = abs(s)
    if a >= 1:
        return f"{s:.2f} s"
    if a >= 1e-3:
        return f"{s * 1e3:.1f} ms"
    return f"{s * 1e6:.0f} µs"


def median_ref_diff(refs: List[dict]) -> Optional[float]:
    diffs = [r["diff_s"] for r in refs if r.get("diff_s") is not None]
    return statistics.median(diffs) if diffs else None


def assess(srv: dict, gps: Optional[dict], probe: dict, refs: Optional[List[dict]] = None,
           server_type: str = "auto") -> dict:
    """Turn raw state into a headline and a list of pass/warn/fail checks.

    srv: {"kind": "chrony"|"ntpd"|None, "ok": bool, "error": str|None, "tracking": dict|None, "sources": [...]}
    gps: gpsd snapshot, or None when this server has no GPS to show.
    """
    checks: List[dict] = []

    def add(cid, label, state, detail):
        checks.append({"id": cid, "label": label, "state": state, "detail": detail})

    kind = srv.get("kind")
    tr = srv.get("tracking")
    srcs = srv.get("sources") or []
    daemon_ok = bool(kind and srv.get("ok") and tr)
    gps_expected = gps is not None
    explicit_fail = server_type in ("chrony", "ntpd") and not daemon_ok
    kind_name = kind or (server_type if server_type in ("chrony", "ntpd") else "the NTP daemon")

    # how the dashboard reads the server
    if kind and daemon_ok:
        add("daemon", kind, "ok", "reachable")
    elif kind and not daemon_ok:
        add("daemon", kind, "bad", srv.get("error") or "no data yet")
    elif explicit_fail:
        add("daemon", server_type, "bad", srv.get("error") or "no data yet")
    elif server_type == "auto":
        add("daemon", "Server details", "warn", "chrony and ntpd not readable · basic view only")

    synced = bool(tr and tr.get("synced"))
    local_ref = bool(tr and tr.get("local_clock"))
    selected = next((s for s in srcs if s["state"] == "*"), None)
    if selected:
        refclock_selected = selected["mode"] == "#"
    else:
        refclock_selected = bool(tr and tr["stratum"] == 1 and not local_ref)

    # time source
    if not tr:
        add("sync", "Time source", "unknown", "no data")
    elif not synced:
        add("sync", "Time source", "bad", "not synchronised")
    elif local_ref:
        add("sync", "Time source", "warn", "free-running on local clock")
    elif refclock_selected:
        name = selected["name"] if selected else tr["ref_name"]
        add("sync", "Time source", "ok", f"{name} · stratum {tr['stratum']}")
    else:
        name = selected["name"] if selected else tr["ref_name"]
        add("sync", "Time source", "warn" if gps_expected else "ok",
            f"{'network: ' if gps_expected else ''}{name} · stratum {tr['stratum']}")

    # upstream servers (only interesting when time comes from the network)
    usable: List[dict] = []
    if daemon_ok and not refclock_selected and kind:
        usable = usable_upstream(srcs)
        bad_src = [s for s in srcs if s["state"] == "x"]
        if len(usable) >= 3:
            state, detail = "ok", f"{len(usable)} reachable"
        elif usable:
            state, detail = "warn", f"only {len(usable)} reachable · 3 or more recommended"
        else:
            state, detail = "bad", "none reachable"
        if bad_src:
            state = "warn" if state == "ok" else state
            detail += f" · {len(bad_src)} flagged as wrong"
        add("upstream", "Upstream servers", state, detail)

    # reference clock (GPS/PPS/...)
    ref = find_refclock(srcs) if daemon_ok else None
    if ref:
        if ref["reach"] == 0:
            add("pps", "Reference clock", "bad", f"{ref['name']} · no samples")
        elif ref["state"] == "*":
            add("pps", "Reference clock", "ok", f"{ref['name']} · reach {ref['reach']:o}")
        elif ref["state"] in ("+", "-"):
            add("pps", "Reference clock", "warn", f"{ref['name']} · present, not selected")
        else:
            add("pps", "Reference clock", "warn", f"{ref['name']} · flagged '{ref['state']}'")
    elif gps_expected and daemon_ok:
        add("pps", "Reference clock", "warn", "none configured in the NTP daemon")

    # GPS
    fix = 0
    if gps_expected:
        if not gps["connected"]:
            add("gps", "GPS fix", "warn", "gpsd unreachable")
        else:
            tpv = gps.get("tpv") or {}
            fix = tpv.get("mode") or 0
            if gps.get("tpv_age_s") is not None and gps["tpv_age_s"] > 15:
                fix = 0
            sats = gps.get("sats") or []
            used = sum(1 for s in sats if s.get("used"))
            if fix >= 3:
                add("gps", "GPS fix", "ok", f"3D · {used} of {len(sats)} satellites")
            elif fix == 2:
                add("gps", "GPS fix", "warn", f"2D only · {used} of {len(sats)} satellites")
            else:
                add("gps", "GPS fix", "bad", "no fix")

    # leap
    if tr:
        leap = tr["leap_status"]
        state = "ok" if leap == "Normal" else ("warn" if leap in ("Insert second", "Delete second") else "bad")
        add("leap", "Leap status", state, leap)

    # error bound (what the server tells its clients)
    me = max_error_s(tr)
    if me is not None:
        lim_ok, lim_warn = (1e-3, 50e-3) if refclock_selected else (50e-3, 250e-3)
        label = lambda x: f"{x * 1e3:g} ms"
        if me <= lim_ok:
            add("bound", "Error bound", "ok", f"within {label(lim_ok)}")
        elif me <= lim_warn:
            add("bound", "Error bound", "warn", f"within {label(lim_warn)}")
        else:
            add("bound", "Error bound", "bad", f"above {label(lim_warn)}")

    # NTP service as seen from the dashboard
    if probe.get("samples", 0) == 0:
        add("probe", "NTP service", "unknown", "waiting for first probe")
    elif not probe["ok"] and (probe.get("loss_pct") or 0) >= 99:
        add("probe", "NTP service", "bad", probe.get("error") or "no reply")
    elif (probe.get("loss_pct") or 0) > 10:
        add("probe", "NTP service", "warn", f"{probe['loss_pct']:.0f}% packet loss")
    else:
        add("probe", "NTP service", "ok", "answering on UDP %d" % probe["port"])

    # agreement with public servers
    live_refs = [r for r in (refs or []) if r.get("diff_s") is not None]
    if refs:
        if not live_refs:
            add("public", "Public servers", "unknown", "waiting for first answers")
        else:
            med = median_ref_diff(live_refs)
            a = abs(med)
            state = "ok" if a <= 20e-3 else ("warn" if a <= 100e-3 else "bad")
            add("public", "Public servers", state,
                f"{'+' if med > 0 else '−' if med < 0 else ''}{fmt_s(a)} vs {len(live_refs)} server{'s' if len(live_refs) != 1 else ''}")

    # headline
    if not tr and explicit_fail:
        code, level, title = "daemon_down", "bad", f"Can't reach {kind_name}"
    elif not tr and probe.get("samples", 0) and not probe["ok"] and (probe.get("loss_pct") or 0) >= 99:
        code, level, title = "no_answer", "bad", "No answer from the NTP server"
    elif not tr:
        code, level, title = "connecting", "unknown", "Connecting…"
    elif kind and not daemon_ok:
        code, level, title = "daemon_down", "bad", f"Can't reach {kind_name}"
    elif not synced:
        code, level, title = "unsynchronised", "bad", "Server is not synchronised"
    elif local_ref:
        code, level, title = "free_running", "warn", "Free-running on its local clock"
    elif refclock_selected:
        if gps_expected and fix < 2:
            code, level, title = "gps_holdover", "warn", "Reference clock selected, but GPS fix is missing"
        else:
            code, level = "gps_locked", "ok"
            title = "Locked to GPS" if gps_expected else "Locked to a reference clock"
    elif gps_expected:
        code, level, title = "network_sync", "warn", "Using network time, not GPS"
    else:
        code, level, title = "network_synced", "ok", "Synchronised to network time"
    attention = sum(1 for c in checks if c["state"] in ("warn", "bad"))
    if level == "ok" and attention:
        level = "warn"
    return {"level": level, "code": code, "title": title, "attention": attention, "checks": checks,
            "usable_sources": len(usable)}


class Monitor:
    def __init__(self, cfg):
        self.cfg = cfg
        self.store = Store(os.path.join(cfg.data_dir, "ntp-dashboard.sqlite"), cfg.history_days)
        self.gps = GpsdClient(cfg.gpsd_host, cfg.gpsd_port) if cfg.gpsd_mode != "false" else None
        self.probe = NtpProber(cfg.ntp_host, cfg.ntp_port, cfg.ntp_interval, cfg.ntp_timeout)
        ref_max_age = cfg.reference_interval * 3 + 30
        self.refs = [self._make_ref(spec, ref_max_age) for spec in cfg.reference_servers]
        self._lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._kind: Optional[str] = None  # which daemon reader worked, once known
        self._srv: dict = {"kind": None, "ok": False, "error": "waiting for first poll", "ts": None,
                           "tracking": None, "sources": [], "serverstats": None,
                           "ntp_rate": None, "drop_rate": None}
        self._prev_ss = None
        self._prev: Dict[str, object] = {}
        self._probe_state = None
        self._gps_seen = False

    def _make_ref(self, spec: str, max_age: float) -> NtpProber:
        host, port = split_hostport(spec)
        return NtpProber(host, port, self.cfg.reference_interval, self.cfg.ntp_timeout,
                         max_age=max_age, label=spec)

    # ---- lifecycle
    def start(self) -> None:
        self.store.add_event("info", "start", "Dashboard started")
        if self.gps:
            self.gps.start()
        self.probe.start()
        for r in self.refs:
            r.start()
        threading.Thread(target=self._server_loop, name="server", daemon=True).start()
        threading.Thread(target=self._record_loop, name="record", daemon=True).start()

    def stop(self) -> None:
        self._stop_evt.set()
        self.probe.stop()
        for r in self.refs:
            r.stop()
        if self.gps:
            self.gps.stop()

    # ---- reading the NTP daemon
    def poll_server(self) -> None:
        cfg, now = self.cfg, time.time()
        if cfg.server_type == "probe":
            return
        kinds = [self._kind] if self._kind else (
            ["chrony", "ntpd"] if cfg.server_type == "auto" else [cfg.server_type])
        errors, snap, found = [], None, None
        for k in kinds:
            try:
                snap, found = FETCHERS[k](cfg), k
                break
            except BACKEND_ERRORS as e:
                errors.append(f"{k}: {e}")
        if snap is None:
            with self._lock:
                self._srv.update(kind=self._kind, ok=False, error="; ".join(errors), ts=now)
            return
        self._kind = found
        ss = snap["serverstats"]
        ntp_rate = drop_rate = None
        if ss and self._prev_ss:
            pts, prev = self._prev_ss
            dt = now - pts
            if dt > 0 and ss["ntp_rx"] >= prev["ntp_rx"]:
                ntp_rate = (ss["ntp_rx"] - prev["ntp_rx"]) / dt
                drop_rate = (ss.get("ntp_dropped", 0) - prev.get("ntp_dropped", 0)) / dt
        self._prev_ss = (now, ss) if ss else None
        with self._lock:
            self._srv = {"kind": found, "ok": True, "error": None, "ts": now,
                         "tracking": snap["tracking"], "sources": snap["sources"],
                         "serverstats": ss, "ntp_rate": ntp_rate, "drop_rate": drop_rate}

    def _server_loop(self) -> None:
        while not self._stop_evt.is_set():
            try:
                self.poll_server()
            except Exception:
                log.exception("server poll failed")
            self._stop_evt.wait(self.cfg.poll_interval)

    # ---- status
    def _position(self, gps: Optional[dict]):
        if self.cfg.hide_position or not gps:
            return None
        tpv = gps.get("tpv") or {}
        if (tpv.get("mode") or 0) < 2 or tpv.get("lat") is None or tpv.get("lon") is None:
            return None
        d = self.cfg.coord_decimals
        return round(tpv["lat"], d), round(tpv["lon"], d)

    def _gps_visible(self, gps: Optional[dict], sources: List[dict]) -> bool:
        """Show GPS panels when the user asked for them, or when a GPS is detected."""
        if gps is None or self.cfg.gpsd_mode == "false":
            return False
        if gps["connected"]:
            self._gps_seen = True
        if self.cfg.gpsd_mode == "true":
            return True
        return self._gps_seen or any(s["mode"] == "#" for s in sources)

    def _references(self, server_off: Optional[float]) -> List[dict]:
        out = []
        for p in self.refs:
            s = p.summary()
            off = s["offset_s"]
            sv = s["server"] or {}
            out.append({
                "host": p.label, "ok": s["ok"], "samples": s["samples"], "rtt_s": s["rtt_s"],
                "stratum": sv.get("stratum"), "ref_id": sv.get("ref_id"), "loss_pct": s["loss_pct"],
                "error": s["error"], "offset_s": off,
                "diff_s": (server_off - off) if server_off is not None and off is not None else None,
            })
        return out

    def status(self) -> dict:
        now = time.time()
        with self._lock:
            srv = dict(self._srv)
        probe = self.probe.summary()
        # no daemon access: build a basic view from what the NTP port itself reveals
        basic = not srv["tracking"] and self.cfg.server_type in ("auto", "probe")
        if basic:
            srv = dict(srv, kind=None, tracking=probe_tracking(probe), sources=[])
        tr, sources = srv["tracking"], srv["sources"] or []

        gps_raw = self.gps.snapshot() if self.gps else None
        gps_shown = gps_raw if self._gps_visible(gps_raw, sources) else None
        refs = self._references(self.probe.best_offset_s())
        a = assess(srv, gps_shown, probe, refs, self.cfg.server_type)
        ref = find_refclock(sources)

        gps_out = None
        if gps_shown is not None:
            gps = gps_shown
            pos = self._position(gps)
            tpv = gps.get("tpv") or {}
            sats = []
            for raw in gps.get("sats") or []:
                s = normalize_sat(raw)
                if pos and s["az"] is not None and s["el"] is not None and s["alt_km"]:
                    s["sub_lat"], s["sub_lon"] = sub_satellite_point(pos[0], pos[1], s["az"], s["el"], s["alt_km"])
                sats.append(s)
            sats.sort(key=lambda s: (not s["used"], -(s["snr"] or 0)))
            gps_out = {
                "connected": gps["connected"], "error": gps["error"], "version": gps["version"],
                "fix_mode": tpv.get("mode"), "tpv_age_s": gps["tpv_age_s"], "sky_age_s": gps["sky_age_s"],
                "time": tpv.get("time"),
                "lat": pos[0] if pos else None, "lon": pos[1] if pos else None,
                "alt_m": None if self.cfg.hide_position else (tpv.get("altHAE") if tpv.get("altHAE") is not None else tpv.get("alt")),
                "position_hidden": self.cfg.hide_position,
                "epx": tpv.get("epx"), "epy": tpv.get("epy"), "epv": tpv.get("epv"),
                "dops": gps["dops"], "devices": gps["devices"], "pps": gps["pps"],
                "pps_age_s": gps["pps_age_s"],
                "satellites": sats,
                "sats_seen": len(sats), "sats_used": sum(1 for s in sats if s["used"]),
            }

        accuracy = None
        if tr:
            accuracy = {
                "last_offset_s": tr["last_offset_s"], "rms_offset_s": tr["rms_offset_s"],
                "jitter_s": tr.get("jitter_s"),
                "system_error_s": abs(tr["system_correction_s"]) if tr["system_correction_s"] is not None else None,
                "root_delay_s": tr["root_delay_s"], "root_dispersion_s": tr["root_dispersion_s"],
                "max_error_s": max_error_s(tr), "skew_ppm": tr["skew_ppm"], "freq_ppm": tr["freq_ppm"],
                "resid_freq_ppm": tr["resid_freq_ppm"], "update_interval_s": tr["update_interval_s"],
                "stratum": tr["stratum"], "ref_name": tr["ref_name"], "leap": tr["leap_status"],
                "ref_age_s": now - tr["ref_time"] if tr.get("ref_time") else None,
                "pps_name": ref["name"] if ref else None,
                "pps_offset_s": ref["est_offset_s"] if ref else None,
                "pps_stddev_s": ref["std_dev_s"] if ref else None,
            }

        ss = srv.get("serverstats")
        mode = srv["kind"] or "probe"
        return {
            "version": __version__, "now_ms": now * 1000.0,
            "banner": {k: a[k] for k in ("level", "code", "title", "attention")},
            "checks": a["checks"],
            "capabilities": {"mode": mode, "details": bool(srv["kind"]), "gps": gps_out is not None,
                             "sources": bool(sources), "serverstats": bool(ss),
                             "references": bool(refs), "server_type": self.cfg.server_type},
            "server": {"kind": srv["kind"], "ok": bool(srv["ok"]), "error": srv["error"],
                       "age_s": now - srv["ts"] if srv.get("ts") else None, "sources": sources,
                       "usable_sources": a["usable_sources"],
                       "serverstats": dict(ss, ntp_rate=srv.get("ntp_rate"), drop_rate=srv.get("drop_rate")) if ss else None},
            "accuracy": accuracy, "gps": gps_out, "probe": probe, "references": refs,
            "events": self.store.events(30),
        }

    def time_info(self) -> dict:
        now_ns = time.time_ns()
        off = self.probe.best_offset_s()
        host_ms = now_ns / 1e6
        s = self.probe.summary()
        return {"ntp_ms": host_ms + (off * 1e3 if off is not None else 0.0), "host_ms": host_ms,
                "offset_ms": off * 1e3 if off is not None else None,
                "source": "probe" if off is not None else "host",
                "probe_rtt_ms": s["rtt_s"] * 1e3 if s["rtt_s"] is not None else None}

    # ---- history recording and events
    def _metrics(self, st: dict) -> Dict[str, Optional[float]]:
        m: Dict[str, Optional[float]] = {}
        acc, gps, pr = st["accuracy"], st["gps"], st["probe"]
        if acc:
            m.update({"offset.last": acc["last_offset_s"], "offset.rms": acc["rms_offset_s"],
                      "offset.jitter": acc["jitter_s"],
                      "pps.offset": acc["pps_offset_s"], "pps.stddev": acc["pps_stddev_s"],
                      "max_error": acc["max_error_s"], "root.dispersion": acc["root_dispersion_s"],
                      "freq.ppm": acc["freq_ppm"], "freq.skew": acc["skew_ppm"], "ref.stratum": acc["stratum"]})
        ss = st["server"]["serverstats"]
        if ss:
            m["ntp.rate"], m["ntp.drop_rate"] = ss.get("ntp_rate"), ss.get("drop_rate")
        if st["server"]["sources"]:
            m["sources.usable"] = st["server"]["usable_sources"]
        if gps and gps["connected"]:
            m.update({"gps.seen": gps["sats_seen"], "gps.used": gps["sats_used"],
                      "gps.hdop": gps["dops"].get("hdop"), "gps.fix": gps["fix_mode"]})
        m.update({"probe.rtt": pr["rtt_s"], "probe.jitter": pr["jitter_s"],
                  "probe.offset": pr["offset_s"], "probe.loss": pr["loss_pct"]})
        for r in st["references"]:
            if r["diff_s"] is not None:
                m[REF_PREFIX + r["host"]] = r["diff_s"]
        return m

    def _detect_events(self, st: dict) -> None:
        srv, gps = st["server"], st["gps"]
        sel = next((s["name"] for s in srv["sources"] if s["state"] == "*"), None)
        kind = srv["kind"] or "NTP daemon"
        cur = {"banner": st["banner"]["code"], "source": sel,
               "fix": gps["fix_mode"] if gps and gps["connected"] else None,
               "gpsd": gps["connected"] if gps else None, "daemon": srv["ok"] or not srv["kind"]}
        prev = self._prev
        if prev:
            ev = self.store.add_event
            if cur["daemon"] != prev["daemon"]:
                ev("ok" if cur["daemon"] else "bad", "daemon",
                   f"{kind} reachable again" if cur["daemon"] else f"Lost contact with {kind}")
            if cur["banner"] != prev["banner"]:
                ev(st["banner"]["level"], "status", st["banner"]["title"])
            if cur["source"] != prev["source"] and cur["daemon"] and prev["daemon"]:
                ev("info", "source", f"Selected source changed: {prev['source'] or 'none'} → {cur['source'] or 'none'}")
            if cur["gpsd"] != prev["gpsd"] and cur["gpsd"] is not None and prev["gpsd"] is not None:
                ev("ok" if cur["gpsd"] else "warn", "gpsd", "gpsd connected" if cur["gpsd"] else "Lost contact with gpsd")
            if cur["fix"] != prev["fix"] and cur["fix"] is not None and prev["fix"] is not None:
                names = {0: "unknown", 1: "none", 2: "2D", 3: "3D"}
                ev("ok" if cur["fix"] >= 3 else "warn", "gps",
                   f"GPS fix changed: {names.get(prev['fix'], prev['fix'])} → {names.get(cur['fix'], cur['fix'])}")
        self._prev = cur
        pr = st["probe"]
        if pr["samples"] >= 3:
            recent = list(self.probe._samples)[-3:]
            state = "ok" if all(s["ok"] for s in recent) else ("fail" if not any(s["ok"] for s in recent) else self._probe_state)
            if state != self._probe_state:
                if self._probe_state is not None:
                    self.store.add_event("ok" if state == "ok" else "bad", "probe",
                                         "NTP service answering again" if state == "ok" else "NTP service stopped answering")
                self._probe_state = state

    def _record_loop(self) -> None:
        last_prune = 0.0
        while not self._stop_evt.wait(self.cfg.sample_interval):
            try:
                st = self.status()
                self.store.add_samples(time.time(), self._metrics(st))
                self._detect_events(st)
                if time.time() - last_prune > 3600:
                    self.store.prune()
                    last_prune = time.time()
            except Exception:
                log.exception("recording failed")

    def history(self, metrics: List[str], seconds: int) -> Dict[str, list]:
        seconds = max(60, min(seconds, self.cfg.history_days * 86400))
        now = time.time()
        allowed = [m for m in metrics if m in METRICS or
                   (m.startswith(REF_PREFIX) and m[len(REF_PREFIX):] in self.cfg.reference_servers)]
        return self.store.history(allowed, now - seconds, now)
