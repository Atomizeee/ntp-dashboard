"""Minimal SNTP client used to measure latency and clock offset to the NTP server."""
from __future__ import annotations

import logging
import socket
import statistics
import struct
import threading
import time
from collections import deque
from typing import Optional

log = logging.getLogger("ntpprobe")

NTP_UNIX_DELTA = 2208988800
_FMT = "!BBbbII4sQQQQ"  # 48 bytes


class NtpError(Exception):
    pass


def split_hostport(spec: str, default_port: int = 123) -> tuple:
    """'pool.ntp.org' -> ('pool.ntp.org', 123); 'host:1123' and '[::1]:1123' are accepted too."""
    spec = spec.strip()
    if spec.startswith("["):
        host, _, rest = spec[1:].partition("]")
        return host, int(rest[1:]) if rest.startswith(":") and rest[1:].isdigit() else default_port
    if spec.count(":") == 1:
        host, _, port = spec.partition(":")
        if port.isdigit():
            return host, int(port)
    return spec, default_port


def ns_to_ntp64(ns: int) -> int:
    sec = ns // 1_000_000_000 + NTP_UNIX_DELTA
    frac = ((ns % 1_000_000_000) << 32) // 1_000_000_000
    return ((sec & 0xFFFFFFFF) << 32) | frac


def ntp64_to_ns(v: int) -> int:
    sec = v >> 32
    frac = v & 0xFFFFFFFF
    if sec < NTP_UNIX_DELTA:  # NTP era 1 (after Feb 2036)
        sec += 1 << 32
    return (sec - NTP_UNIX_DELTA) * 1_000_000_000 + ((frac * 1_000_000_000 + (1 << 31)) >> 32)


def build_request(t1_ns: int) -> tuple[bytes, int]:
    t1 = ns_to_ntp64(t1_ns)
    pkt = struct.pack(_FMT, 0x23, 0, 0, 0, 0, 0, b"\0\0\0\0", 0, 0, 0, t1)
    return pkt, t1


def _short(v: int) -> float:
    return v / 65536.0


def _ref_id(stratum: int, raw: bytes) -> str:
    if stratum <= 1:
        return raw.rstrip(b"\0").decode("ascii", "replace")
    return ".".join(str(b) for b in raw)


def parse_response(data: bytes, expected_origin: int, t1_ns: int, t4_ns: int,
                   rtt_ns: int) -> dict:
    if len(data) < 48:
        raise NtpError(f"short reply ({len(data)} bytes)")
    (b0, stratum, poll, precision, root_delay, root_disp, refid,
     _ref_ts, orig, rx, tx) = struct.unpack(_FMT, data[:48])
    li, vn, mode = b0 >> 6, (b0 >> 3) & 7, b0 & 7
    if mode != 4:
        raise NtpError(f"unexpected mode {mode}")
    if orig != expected_origin:
        raise NtpError("origin timestamp mismatch")
    if stratum == 0:
        raise NtpError("kiss-o'-death: " + _ref_id(0, refid))
    if tx == 0 or rx == 0:
        raise NtpError("server sent zero timestamps")
    t2, t3 = ntp64_to_ns(rx), ntp64_to_ns(tx)
    offset_ns = ((t2 - t1_ns) + (t3 - t4_ns)) / 2.0
    delay_ns = max(0, rtt_ns - (t3 - t2))
    return {
        "offset_s": offset_ns / 1e9,
        "delay_s": delay_ns / 1e9,
        "stratum": stratum,
        "ref_id": _ref_id(stratum, refid),
        "leap": li,
        "version": vn,
        "poll": poll,
        "precision_s": 2.0 ** precision,
        "root_delay_s": _short(root_delay),
        "root_dispersion_s": _short(root_disp),
        "synced": li != 3 and stratum < 16,
    }


def query_once(host: str, port: int, timeout: float) -> dict:
    infos = socket.getaddrinfo(host, port, 0, socket.SOCK_DGRAM)
    family, stype, proto, _canon, addr = infos[0]
    with socket.socket(family, stype, proto) as sock:
        sock.settimeout(timeout)
        t1_ns = time.time_ns()
        pkt, t1_ntp = build_request(t1_ns)
        p1 = time.perf_counter_ns()
        sock.sendto(pkt, addr)
        data, _ = sock.recvfrom(512)
        p4 = time.perf_counter_ns()
        t4_ns = time.time_ns()
    return parse_response(data, t1_ntp, t1_ns, t4_ns, p4 - p1)


class NtpProber(threading.Thread):
    def __init__(self, host: str, port: int, interval: float, timeout: float,
                 max_age: float = 90.0, label: Optional[str] = None):
        super().__init__(name=f"ntpprobe-{host}", daemon=True)
        self.label = label or host
        self.host, self.port, self.interval, self.timeout = host, port, interval, timeout
        self.max_age = max_age
        self._lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._samples: deque = deque(maxlen=64)
        self._server: Optional[dict] = None
        self._last_error: Optional[str] = None

    def stop(self) -> None:
        self._stop_evt.set()

    def run(self) -> None:
        while not self._stop_evt.is_set():
            self.probe()
            self._stop_evt.wait(self.interval)

    def probe(self) -> None:
        now = time.time()
        try:
            r = query_once(self.host, self.port, self.timeout)
            with self._lock:
                self._samples.append({"ts": now, "ok": True, "delay_s": r["delay_s"],
                                      "offset_s": r["offset_s"]})
                self._server = r
                self._last_error = None
        except socket.timeout:
            self._fail(now, "timeout")
        except (OSError, NtpError) as e:
            self._fail(now, str(e) or e.__class__.__name__)

    def _fail(self, ts: float, msg: str) -> None:
        with self._lock:
            self._samples.append({"ts": ts, "ok": False})
            self._last_error = msg

    def best_offset_s(self, max_age: Optional[float] = None) -> Optional[float]:
        """Median offset of the lowest-delay recent samples (server time - local time)."""
        max_age = self.max_age if max_age is None else max_age
        now = time.time()
        with self._lock:
            ok = [s for s in list(self._samples)[-16:]
                  if s["ok"] and now - s["ts"] <= max_age]
        if not ok:
            return None
        best = sorted(ok, key=lambda s: s["delay_s"])[:5]
        return statistics.median(s["offset_s"] for s in best)

    def summary(self) -> dict:
        now = time.time()
        with self._lock:
            samples = list(self._samples)
            server = dict(self._server) if self._server else None
            err = self._last_error
        window = samples[-30:]
        okw = [s for s in window if s["ok"]]
        delays = [s["delay_s"] for s in okw]
        last_ok = next((s for s in reversed(samples) if s["ok"]), None)
        return {
            "host": self.host, "port": self.port,
            "ok": bool(samples and samples[-1]["ok"]),
            "samples": len(window),
            "loss_pct": 100.0 * (len(window) - len(okw)) / len(window) if window else None,
            "rtt_s": delays[-1] if delays else None,
            "rtt_min_s": min(delays) if delays else None,
            "rtt_avg_s": statistics.fmean(delays) if delays else None,
            "jitter_s": statistics.stdev(delays) if len(delays) >= 2 else None,
            "offset_s": self.best_offset_s(),
            "last_ok_age_s": now - last_ok["ts"] if last_ok else None,
            "error": err,
            "server": server,
        }
