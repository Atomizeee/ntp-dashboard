"""Connectivity check: python -m app.selftest

Runs every data source once and prints what worked, with hints for what did not.
Exit code 0 when everything that is needed works.
"""
import json
import socket
import time

from . import ntpq
from .backends import chrony_snapshot, ntpd_snapshot
from .chrony import ChronyError, run_chronyc
from .config import Config
from .ntpprobe import query_once


def _line(ok, name, detail):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    return ok


def _warn(name, detail):
    print(f"[WARN] {name}: {detail}")


def _check_chrony(cfg):
    try:
        snap = chrony_snapshot(cfg)
    except (ChronyError, ValueError) as e:
        return False, str(e), None
    n = len(snap["sources"])
    extra = "" if snap["serverstats"] else " (client statistics are not available remotely, that is normal)"
    return True, f"{n} source(s), stratum {snap['tracking']['stratum']}{extra}", snap


def _check_ntpd(cfg):
    try:
        snap = ntpd_snapshot(cfg)
    except (ntpq.NtpqError, OSError) as e:
        return False, str(e), None
    return True, f"{len(snap['sources'])} source(s), stratum {snap['tracking']['stratum']}", snap


def main() -> int:
    cfg = Config.from_env()
    print(f"server {cfg.server_host}  type {cfg.server_type}  gpsd {cfg.gpsd_host}:{cfg.gpsd_port} ({cfg.gpsd_mode})  "
          f"ntp port {cfg.ntp_port}  public servers: {', '.join(cfg.reference_servers) or 'none'}\n")
    bad = 0

    # 1) the NTP service itself, the one thing every setup needs
    try:
        r = query_once(cfg.ntp_host, cfg.ntp_port, cfg.ntp_timeout * 2)
        _line(True, "NTP service", f"stratum {r['stratum']}, ref {r['ref_id']}, rtt {r['delay_s']*1e3:.3f} ms, "
                                   f"offset to this machine {r['offset_s']*1e3:+.3f} ms")
    except Exception as e:  # noqa: BLE001 - any failure is reported the same way
        bad += not _line(False, "NTP service", e)
        print("      hint: UDP 123 must be reachable from this machine, and the server must allow it as a client.")

    # 2) detailed data from the daemon
    if cfg.server_type == "probe":
        print("[INFO] daemon details: skipped (SERVER_TYPE=probe, only the NTP port is read)")
    else:
        order = {"auto": ("chrony", "ntpd"), "chrony": ("chrony",), "ntpd": ("ntpd",)}[cfg.server_type]
        found, errs = None, []
        for kind in order:
            ok, detail, _snap = (_check_chrony if kind == "chrony" else _check_ntpd)(cfg)
            if ok:
                _line(True, kind, detail)
                found = kind
                break
            errs.append((kind, detail))
        if not found:
            for kind, detail in errs:
                if cfg.server_type == "auto":
                    _warn(kind, detail)
                else:
                    bad += not _line(False, kind, detail)
            if cfg.server_type == "auto":
                print("      The dashboard still works in basic view (stratum, latency, browser clock, public servers).")
            print("      hint chrony: set 'bindcmdaddress 0.0.0.0' and 'cmdallow <your LAN>' in chrony.conf, restart it,\n"
                  "                   allow UDP 323. 'chronyc -h <host> tracking' must work from this machine.\n"
                  "      hint ntpd:   allow queries from this machine with a 'restrict <ip> nomodify notrap nopeer' line\n"
                  "                   (no 'noquery'). Test with 'ntpq -p <host>'.\n"
                  "      Set SERVER_TYPE=probe to silence this if you only want the basic view.")

    # 3) gpsd (optional)
    if cfg.gpsd_mode == "false":
        print("[INFO] gpsd: disabled (GPSD_ENABLED=false)")
    else:
        try:
            with socket.create_connection((cfg.gpsd_host, cfg.gpsd_port), timeout=5) as s:
                s.settimeout(8)
                s.sendall(b'?WATCH={"enable":true,"json":true}\n')
                seen, t0, buf = set(), time.time(), b""
                while time.time() - t0 < 8 and not {"TPV", "SKY"} <= seen:
                    buf += s.recv(4096)
                    for ln in buf.split(b"\n")[:-1]:
                        try:
                            seen.add(json.loads(ln).get("class"))
                        except ValueError:
                            pass
            ok = {"TPV", "SKY"} <= seen
            if ok:
                _line(True, "gpsd", "receiving TPV and SKY")
            else:
                bad += not _line(False, "gpsd", f"connected but only saw {sorted(seen)}")
        except OSError as e:
            if cfg.gpsd_mode == "true":
                bad += not _line(False, "gpsd", e)
                print("      hint: gpsd listens on 127.0.0.1 by default; see the README to open it to this machine.")
            else:
                print(f"[INFO] gpsd: not reachable ({e}). Fine if this server has no GPS; the satellite panels stay hidden.")

    # 4) public servers (optional)
    for host in cfg.reference_servers:
        try:
            r = query_once(host, 123, cfg.ntp_timeout * 2)
            _line(True, f"public {host}", f"stratum {r['stratum']}, rtt {r['delay_s']*1e3:.1f} ms")
        except Exception as e:  # noqa: BLE001
            _warn(f"public {host}", f"{e} (needs outbound UDP 123; set REFERENCE_SERVERS= to switch this off)")

    print("\nAll good." if not bad else f"\n{bad} check(s) failed.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
