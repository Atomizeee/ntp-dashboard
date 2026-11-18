"""Read an ntpd server over the network using NTP control messages (mode 6).

This is the protocol behind `ntpq -p` and `ntpq -c rv`, implemented with the standard library
so the container needs no extra packages. It is read-only: only READSTAT and READVAR are sent.
ntpd must allow queries from the dashboard host (a `restrict` line without `noquery`).
"""
from __future__ import annotations

import socket
import struct
import time
from typing import Dict, List, Optional, Tuple

OP_READSTAT = 1
OP_READVAR = 2
NTP_UNIX_DELTA = 2208988800
MAX_ASSOCIATIONS = 24

_ERRORS = {
    1: "unspecified error", 2: "permission denied", 3: "bad format", 4: "bad opcode",
    5: "unknown association", 6: "unknown variable", 7: "bad value", 8: "access restricted",
}
_LEAP = {0: "Normal", 1: "Insert second", 2: "Delete second", 3: "Not synchronised"}

# Peer selection code (bits 10-8 of the association status word) -> chrony-style state symbol
_SELECT_STATE = {0: "?", 1: "x", 2: "-", 3: "-", 4: "+", 5: "-", 6: "*", 7: "*"}


class NtpqError(Exception):
    pass


class NtpqSession:
    """One UDP socket, several control requests."""

    def __init__(self, host: str, port: int = 123, timeout: float = 2.0):
        self.host, self.port, self.timeout = host, port, timeout
        self._seq = int(time.time()) & 0x7FFF
        self._sock: Optional[socket.socket] = None
        self._addr = None

    def __enter__(self) -> "NtpqSession":
        family, stype, proto, _canon, addr = socket.getaddrinfo(
            self.host, self.port, 0, socket.SOCK_DGRAM)[0]
        self._sock = socket.socket(family, stype, proto)
        self._addr = addr
        return self

    def __exit__(self, *exc) -> None:
        if self._sock:
            self._sock.close()

    def request(self, opcode: int, assoc: int = 0) -> Tuple[int, bytes]:
        """Send one request, reassemble the (possibly fragmented) reply: (status word, data)."""
        assert self._sock is not None
        self._seq = (self._seq + 1) & 0xFFFF
        seq = self._seq
        pkt = struct.pack("!BBHHHHH", 0x16, opcode, seq, 0, assoc, 0, 0)
        self._sock.sendto(pkt, self._addr)
        frags: Dict[int, bytes] = {}
        end: Optional[int] = None
        status = 0
        deadline = time.monotonic() + self.timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise NtpqError("timeout (ntpd not answering control queries; "
                                "check its 'restrict' lines and the firewall)")
            self._sock.settimeout(left)
            try:
                data, _ = self._sock.recvfrom(2048)
            except socket.timeout:
                continue
            if len(data) < 12 or (data[0] & 7) != 6:
                continue
            _b0, op, rseq, rstatus, rassoc, off, cnt = struct.unpack("!BBHHHHH", data[:12])
            if rseq != seq or not (op & 0x80) or (op & 0x1F) != opcode:
                continue
            if op & 0x40:
                code = rstatus >> 8
                raise NtpqError(_ERRORS.get(code, f"error {code}") +
                                (" (ntpd 'restrict' blocks queries from this host)" if code in (2, 8) else ""))
            if off == 0:
                status = rstatus
            frags[off] = data[12:12 + cnt]
            if not (op & 0x20):
                end = off + cnt
            if end is not None:
                pos = 0
                for o in sorted(frags):
                    if o != pos:
                        break
                    pos += len(frags[o])
                if pos == end:
                    return status, b"".join(frags[o] for o in sorted(frags))

    def associations(self) -> List[Tuple[int, int]]:
        _status, data = self.request(OP_READSTAT, 0)
        return [struct.unpack("!HH", data[i:i + 4]) for i in range(0, len(data) - 3, 4)]

    def variables(self, assoc: int) -> Tuple[int, Dict[str, str]]:
        status, data = self.request(OP_READVAR, assoc)
        return status, parse_vars(data.decode("ascii", "replace"))


def parse_vars(text: str) -> Dict[str, str]:
    """'a=1, b="x, y", c=3' -> {'a': '1', 'b': 'x, y', 'c': '3'}"""
    items, buf, quoted = [], [], False
    for ch in text:
        if ch == '"':
            quoted = not quoted
            buf.append(ch)
        elif ch == "," and not quoted:
            items.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    items.append("".join(buf))
    out: Dict[str, str] = {}
    for item in items:
        item = item.strip()
        if not item:
            continue
        key, _sep, val = item.partition("=")
        val = val.strip()
        if len(val) >= 2 and val[0] == '"' and val[-1] == '"':
            val = val[1:-1]
        out[key.strip()] = val
    return out


def _num(v: Dict[str, str], key: str, scale: float = 1.0) -> Optional[float]:
    try:
        return float(v[key]) * scale
    except (KeyError, ValueError):
        return None


def _int(v: Dict[str, str], key: str, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(float(v[key]))
    except (KeyError, ValueError):
        return default


def parse_leap(text: Optional[str], status: int) -> int:
    """ntpd prints leap as a 2-bit string ('00', '11'); fall back to the status word."""
    if text:
        t = text.strip()
        try:
            if len(t) == 2 and set(t) <= {"0", "1"}:
                return int(t, 2)
            return int(t) & 3
        except ValueError:
            pass
    return (status >> 14) & 3


def parse_reach(text: Optional[str]) -> int:
    """Reach register as an int. ntpq shows it in octal; ntpd may send hex or octal text."""
    if not text:
        return 0
    t = text.strip().lower()
    try:
        if t.startswith("0x") or any(c in "abcdef" for c in t):
            return int(t, 16) & 0xFF
        if all(c in "01234567" for c in t) and int(t, 8) <= 0xFF:
            return int(t, 8)
        return int(t, 10) & 0xFF
    except ValueError:
        return 0


def ntp_hex_to_unix(text: Optional[str]) -> Optional[float]:
    """'0xeb1a2b3c.4d5e6f70' (NTP timestamp) -> unix seconds."""
    if not text or "." not in text:
        return None
    try:
        sec_s, frac_s = text.split(".", 1)
        sec, frac = int(sec_s, 16), int(frac_s, 16)
    except ValueError:
        return None
    if sec == 0:
        return None
    return sec - NTP_UNIX_DELTA + frac / float(1 << (4 * len(frac_s)))


def _clean_refid(refid: str) -> str:
    return refid.strip().strip(".")


def tracking_from_vars(v: Dict[str, str], status: int) -> dict:
    """System variables -> the same shape chrony's `tracking` is normalised to."""
    stratum = _int(v, "stratum", 16)
    leap = parse_leap(v.get("leap"), status)
    refid = (v.get("refid") or "").strip()
    name = _clean_refid(refid)
    synced = leap != 3 and stratum is not None and stratum < 16
    ms = 1e-3
    return {
        "ref_id": refid, "ref_name": name, "stratum": stratum if stratum is not None else 16,
        "ref_time": ntp_hex_to_unix(v.get("reftime") or v.get("reftime ")),
        "system_correction_s": None, "last_offset_s": _num(v, "offset", ms),
        "rms_offset_s": None, "jitter_s": _num(v, "sys_jitter", ms),
        "freq_ppm": _num(v, "frequency"), "resid_freq_ppm": None, "skew_ppm": None,
        "root_delay_s": _num(v, "rootdelay", ms), "root_dispersion_s": _num(v, "rootdisp", ms),
        "update_interval_s": None, "leap_status": _LEAP.get(leap, "Not synchronised"),
        "synced": synced, "local_clock": name.upper() in ("LOCL", "LOCAL"),
        "system": v.get("system"), "version": v.get("version"),
    }


def source_from_vars(assoc: int, status: int, v: Dict[str, str]) -> dict:
    """Peer variables -> one row of the sources table."""
    addr = (v.get("srcadr") or "").strip()
    refclock = addr.startswith("127.127.")
    refid = _clean_refid(v.get("refid") or "")
    hmode = _int(v, "hmode", 3)
    mode = "#" if refclock else ("=" if hmode == 1 else "^")
    name = f"{refid} ({addr})" if refclock and refid else (addr or f"association {assoc}")
    ms = 1e-3
    offset, jitter = _num(v, "offset", ms), _num(v, "jitter", ms)
    return {
        "mode": mode, "state": _SELECT_STATE.get((status >> 8) & 7, "?"), "name": name,
        "stratum": _int(v, "stratum", 16), "poll": _int(v, "hpoll", _int(v, "ppoll", 6)),
        "reach": parse_reach(v.get("reach")), "last_rx_s": None,
        "offset_s": offset, "measured_s": offset, "error_s": None, "delay_s": _num(v, "delay", ms),
        "np": None, "nr": None, "span_s": None, "freq_ppm": None, "freq_skew_ppm": None,
        "est_offset_s": offset, "std_dev_s": jitter,
    }


def snapshot(host: str, port: int = 123, timeout: float = 2.0) -> dict:
    """Read system variables and all associations from an ntpd server."""
    try:
        with NtpqSession(host, port, timeout) as q:
            status, sysvars = q.variables(0)
            if "stratum" not in sysvars:
                raise NtpqError("reply did not contain system variables")
            tracking = tracking_from_vars(sysvars, status)
            sources = []
            for assoc, st in q.associations()[:MAX_ASSOCIATIONS]:
                try:
                    _s, pv = q.variables(assoc)
                except NtpqError:
                    continue
                if pv.get("srcadr"):
                    sources.append(source_from_vars(assoc, st, pv))
    except OSError as e:
        raise NtpqError(str(e) or e.__class__.__name__)
    return {"tracking": tracking, "sources": sources, "serverstats": None}


def raw_dump(host: str, port: int = 123, timeout: float = 2.0) -> str:
    """Human-readable dump of everything read from ntpd (for troubleshooting)."""
    lines = []
    try:
        with NtpqSession(host, port, timeout) as q:
            status, sysvars = q.variables(0)
            lines.append(f"$ ntpq -c rv    (status word 0x{status:04x})")
            lines += [f"  {k}={v}" for k, v in sysvars.items()]
            for assoc, st in q.associations()[:MAX_ASSOCIATIONS]:
                _s, pv = q.variables(assoc)
                lines.append(f"\n$ ntpq -c 'rv {assoc}'    (peer status 0x{st:04x}, selection {(st >> 8) & 7})")
                lines += [f"  {k}={v}" for k, v in pv.items()]
    except (OSError, NtpqError) as e:
        lines.append(f"ERROR: {e}")
    return "\n".join(lines) + "\n"
