"""Configuration, read from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Tuple

SERVER_TYPES = ("auto", "chrony", "ntpd", "probe")
GPSD_MODES = ("auto", "true", "false")
DEFAULT_REFERENCES = ("time.cloudflare.com", "time.google.com")


def _str(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    return default if v is None or v.strip() == "" else v.strip()


def _int(name: str, default: int) -> int:
    try:
        return int(_str(name, str(default)))
    except ValueError:
        raise SystemExit(f"{name} must be an integer")


def _float(name: str, default: float) -> float:
    try:
        return float(_str(name, str(default)))
    except ValueError:
        raise SystemExit(f"{name} must be a number")


def _bool(name: str, default: bool) -> bool:
    v = _str(name).lower()
    if v == "":
        return default
    return v in ("1", "true", "yes", "on")


def _choice(name: str, default: str, allowed: Tuple[str, ...]) -> str:
    v = _str(name, default).lower()
    if v not in allowed:
        raise SystemExit(f"{name} must be one of: {', '.join(allowed)} (got '{v}')")
    return v


def _list(name: str, default: Tuple[str, ...]) -> Tuple[str, ...]:
    """Unset -> default. Set but empty -> empty list (feature switched off)."""
    v = os.environ.get(name)
    if v is None:
        return default
    return tuple(x for x in v.replace(",", " ").split() if x)


@dataclass(frozen=True)
class Config:
    server_name: str = "NTP server"
    # The machine that runs the NTP daemon. CHRONY_HOST is still accepted as an alias.
    server_host: str = "127.0.0.1"
    # auto = try chrony, then ntpd, then fall back to querying the NTP port only
    server_type: str = "auto"
    # chrony (read with chronyc over UDP; needs bindcmdaddress + cmdallow on the server)
    chrony_port: int = 323
    chronyc_bin: str = "chronyc"
    poll_interval: float = 5.0
    # gpsd (JSON over TCP). auto = show GPS panels only when a GPS is detected.
    gpsd_mode: str = "auto"
    gpsd_host: str = "127.0.0.1"
    gpsd_port: int = 2947
    # direct NTP probe (latency, and the reference for the browser clock comparison)
    ntp_host: str = "127.0.0.1"
    ntp_port: int = 123
    ntp_interval: float = 5.0
    ntp_timeout: float = 1.5
    # public servers used as an independent cross-check
    reference_servers: Tuple[str, ...] = DEFAULT_REFERENCES
    reference_interval: float = 64.0
    # web server
    listen_host: str = "0.0.0.0"
    listen_port: int = 8080
    basic_auth_user: str = ""
    basic_auth_pass: str = ""
    # history
    data_dir: str = "/data"
    history_days: int = 7
    sample_interval: float = 10.0
    # privacy
    coord_decimals: int = 2
    hide_position: bool = False
    # map
    map_tile_url: str = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    map_attribution: str = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    debug: bool = False

    # kept so older code and tests that use the previous names keep working
    @property
    def chrony_host(self) -> str:
        return self.server_host

    @property
    def gpsd_enabled(self) -> bool:
        return self.gpsd_mode != "false"

    @classmethod
    def from_env(cls) -> "Config":
        host = _str("SERVER_HOST") or _str("CHRONY_HOST", "127.0.0.1")
        refs = _list("REFERENCE_SERVERS", DEFAULT_REFERENCES)[:4]
        gpsd_raw = _str("GPSD_ENABLED", "auto").lower()
        gpsd_raw = {"1": "true", "yes": "true", "on": "true", "0": "false", "no": "false", "off": "false"}.get(gpsd_raw, gpsd_raw)
        if gpsd_raw not in GPSD_MODES:
            raise SystemExit(f"GPSD_ENABLED must be one of: {', '.join(GPSD_MODES)} (got '{gpsd_raw}')")
        return cls(
            server_name=_str("SERVER_NAME", "NTP server"),
            server_host=host,
            server_type=_choice("SERVER_TYPE", "auto", SERVER_TYPES),
            chrony_port=_int("CHRONY_PORT", 323),
            chronyc_bin=_str("CHRONYC_BIN", "chronyc"),
            poll_interval=max(1.0, _float("POLL_INTERVAL", _float("CHRONY_INTERVAL", 5.0))),
            gpsd_mode=gpsd_raw,
            gpsd_host=_str("GPSD_HOST", host),
            gpsd_port=_int("GPSD_PORT", 2947),
            ntp_host=_str("NTP_HOST", host),
            ntp_port=_int("NTP_PORT", 123),
            ntp_interval=max(1.0, _float("NTP_INTERVAL", 5.0)),
            ntp_timeout=max(0.2, _float("NTP_TIMEOUT", 1.5)),
            reference_servers=refs,
            reference_interval=max(32.0, _float("REFERENCE_INTERVAL", 64.0)),
            listen_host=_str("LISTEN_HOST", "0.0.0.0"),
            listen_port=_int("PORT", 8080),
            basic_auth_user=_str("BASIC_AUTH_USER"),
            basic_auth_pass=_str("BASIC_AUTH_PASS"),
            data_dir=_str("DATA_DIR", "/data"),
            history_days=max(1, _int("HISTORY_DAYS", 7)),
            sample_interval=max(2.0, _float("SAMPLE_INTERVAL", 10.0)),
            coord_decimals=min(6, max(0, _int("COORD_DECIMALS", 2))),
            hide_position=_bool("HIDE_POSITION", False),
            map_tile_url=_str("MAP_TILE_URL", cls.map_tile_url),
            map_attribution=_str("MAP_ATTRIBUTION", cls.map_attribution),
            debug=_bool("DEBUG", False),
        )
