"""Satellite helpers: constellation naming and approximate ground positions.

gpsd only reports where a satellite is in the receiver's sky (azimuth/elevation).
To draw it on a map we compute the point on the Earth's surface directly below it,
assuming a nominal orbit altitude per constellation. That is a visualisation aid,
not an orbit propagation: expect errors of a few hundred km.
"""
from __future__ import annotations

import math
from typing import Optional, Tuple

EARTH_RADIUS_KM = 6371.0

# gnssid -> (name, one-letter prefix, nominal orbit altitude in km)
GNSS = {
    0: ("GPS", "G", 20200.0),
    1: ("SBAS", "S", 35786.0),
    2: ("Galileo", "E", 23222.0),
    3: ("BeiDou", "C", 21528.0),
    4: ("IMES", "I", 0.0),
    5: ("QZSS", "Q", 35786.0),
    6: ("GLONASS", "R", 19130.0),
    7: ("NavIC", "N", 35786.0),
}

# BeiDou GEO/IGSO satellites sit much higher than the MEO ones.
_BEIDOU_HIGH = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 13, 16, 31, 38, 39, 40, 56,
                59, 60, 61, 62, 63}


def guess_from_prn(prn: Optional[int]) -> Tuple[Optional[int], Optional[int]]:
    """Map a gpsd PRN to (gnssid, svid) when the receiver did not report them."""
    if prn is None:
        return None, None
    if 1 <= prn <= 32:
        return 0, prn
    if 33 <= prn <= 64:
        return 1, prn + 87
    if 65 <= prn <= 96:
        return 6, prn - 64
    if 120 <= prn <= 158:
        return 1, prn
    if 173 <= prn <= 182:
        return 4, prn - 172
    if 193 <= prn <= 199:
        return 5, prn - 192
    if 201 <= prn <= 263:
        return 3, prn - 200
    if 301 <= prn <= 336:
        return 2, prn - 300
    return None, None


def orbit_altitude_km(gnssid: Optional[int], svid: Optional[int]) -> Optional[float]:
    info = GNSS.get(gnssid)
    if not info or info[2] <= 0:
        return None
    if gnssid == 3 and svid in _BEIDOU_HIGH:
        return 35786.0
    return info[2]


def normalize_sat(raw: dict) -> dict:
    prn = raw.get("PRN")
    gnssid = raw.get("gnssid")
    svid = raw.get("svid")
    if gnssid is None:
        gnssid, guessed = guess_from_prn(prn)
        if svid is None:
            svid = guessed
    info = GNSS.get(gnssid)
    name, prefix = (info[0], info[1]) if info else ("Unknown", "?")
    num = svid if svid is not None else prn
    snr = raw.get("ss")
    return {
        "id": f"{prefix}{num}" if num is not None else prefix,
        "prn": prn,
        "svid": svid,
        "gnssid": gnssid,
        "gnss": name,
        "az": raw.get("az"),
        "el": raw.get("el"),
        "snr": snr,
        "used": bool(raw.get("used")),
        "alt_km": orbit_altitude_km(gnssid, svid),
    }


def sub_satellite_point(lat: float, lon: float, az: float, el: float,
                        alt_km: float) -> Tuple[float, float]:
    """Point on the ground below a satellite seen at (az, el) from (lat, lon)."""
    el_r = math.radians(el)
    ratio = EARTH_RADIUS_KM * math.cos(el_r) / (EARTH_RADIUS_KM + alt_km)
    lam = math.acos(max(-1.0, min(1.0, ratio))) - el_r  # earth-central angle
    lat1, lon1, brg = math.radians(lat), math.radians(lon), math.radians(az)
    lat2 = math.asin(math.sin(lat1) * math.cos(lam)
                     + math.cos(lat1) * math.sin(lam) * math.cos(brg))
    lon2 = lon1 + math.atan2(math.sin(brg) * math.sin(lam) * math.cos(lat1),
                             math.cos(lam) - math.sin(lat1) * math.sin(lat2))
    lon_d = (math.degrees(lon2) + 540.0) % 360.0 - 180.0
    return math.degrees(lat2), lon_d
