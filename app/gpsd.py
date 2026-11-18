"""Background client for gpsd's JSON protocol (TCP 2947)."""
from __future__ import annotations

import json
import logging
import socket
import threading
import time
from typing import Optional

log = logging.getLogger("gpsd")

_TPV_KEYS = ("mode", "time", "lat", "lon", "alt", "altHAE", "altMSL", "epx", "epy",
             "epv", "ept", "eph", "speed", "track", "device", "status")
_DOP_KEYS = ("hdop", "vdop", "pdop", "gdop", "tdop", "xdop", "ydop")


class GpsdClient(threading.Thread):
    def __init__(self, host: str, port: int):
        super().__init__(name="gpsd", daemon=True)
        self.host, self.port = host, port
        self._lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._s = {"connected": False, "error": "connecting", "connected_since": None,
                   "version": None, "devices": {}, "tpv": None, "tpv_ts": None,
                   "sats": [], "dops": {}, "nsat": None, "usat": None,
                   "sky_ts": None, "pps": None, "pps_ts": None}

    def stop(self) -> None:
        self._stop_evt.set()

    def _set(self, **kw) -> None:
        with self._lock:
            self._s.update(kw)

    def run(self) -> None:
        backoff = 1.0
        while not self._stop_evt.is_set():
            try:
                with socket.create_connection((self.host, self.port), timeout=5) as sock:
                    sock.settimeout(20)
                    sock.sendall(b'?WATCH={"enable":true,"json":true,"pps":true}\n')
                    self._set(connected=True, error=None, connected_since=time.time())
                    backoff = 1.0
                    for raw in sock.makefile("rb"):
                        self.handle(raw)
                        if self._stop_evt.is_set():
                            return
                    raise ConnectionError("gpsd closed the connection")
            except OSError as e:
                msg = "no data from gpsd for 20 s" if isinstance(e, socket.timeout) else (str(e) or e.__class__.__name__)
                self._set(connected=False, error=msg)
            self._stop_evt.wait(backoff)
            backoff = min(backoff * 2, 15.0)

    def handle(self, raw: bytes) -> None:
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        cls = msg.get("class")
        now = time.time()
        with self._lock:
            s = self._s
            if cls == "VERSION":
                s["version"] = msg.get("release")
            elif cls in ("DEVICES", "DEVICE"):
                devs = msg.get("devices") if cls == "DEVICES" else [msg]
                for d in devs or []:
                    if d.get("path"):
                        s["devices"][d["path"]] = {k: d.get(k) for k in
                                                   ("path", "driver", "subtype", "bps", "activated")}
            elif cls == "TPV":
                s["tpv"] = {k: msg.get(k) for k in _TPV_KEYS if k in msg}
                s["tpv_ts"] = now
            elif cls == "SKY":
                for k in _DOP_KEYS:
                    if k in msg:
                        s["dops"][k] = msg[k]
                if "satellites" in msg:
                    s["sats"] = msg["satellites"]
                    s["nsat"], s["usat"] = msg.get("nSat"), msg.get("uSat")
                    s["sky_ts"] = now
            elif cls == "PPS":
                off = ((msg.get("clock_sec", 0) - msg.get("real_sec", 0))
                       + (msg.get("clock_nsec", 0) - msg.get("real_nsec", 0)) / 1e9)
                s["pps"] = {"device": msg.get("device"), "offset_s": off,
                            "precision": msg.get("precision"), "q_err_ps": msg.get("qErr")}
                s["pps_ts"] = now

    def snapshot(self) -> dict:
        now = time.time()
        with self._lock:
            s = json.loads(json.dumps(self._s))  # cheap deep copy
        s["tpv_age_s"] = now - s["tpv_ts"] if s["tpv_ts"] else None
        s["sky_age_s"] = now - s["sky_ts"] if s["sky_ts"] else None
        s["pps_age_s"] = now - s["pps_ts"] if s["pps_ts"] else None
        s["devices"] = list(s["devices"].values())
        return s
