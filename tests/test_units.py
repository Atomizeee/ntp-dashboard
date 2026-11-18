import math
import os
import time
import unittest

from app import backends, chrony, ntpprobe, ntpq, sats
from app.config import Config
from app.monitor import assess, find_refclock, median_ref_diff
from app.store import Store
from tests import fakes

TRACKING = "50505300,PPS,1,1794786858.123456,0.000000000,-0.000000809,0.000000978,-8.256,-0.001,0.064,0.000000001,0.000030739,16.0,Normal"


class Chrony(unittest.TestCase):
    def test_tracking(self):
        t = chrony.parse_tracking(TRACKING + "\n")
        self.assertEqual(t["ref_name"], "PPS")
        self.assertEqual(t["stratum"], 1)
        self.assertAlmostEqual(t["rms_offset_s"], 9.78e-7)
        self.assertEqual(t["leap_status"], "Normal")

    def test_tracking_bad(self):
        with self.assertRaises(chrony.ChronyError):
            chrony.parse_tracking("garbage")

    def test_sources_and_stats(self):
        s = chrony.parse_sources("#,*,PPS,0,4,377,5,0.000000001,0.000000002,0.0000005\n^,-,10.0.0.1,2,6,37,41,-0.001,-0.001,0.024\n")
        self.assertEqual(s[0]["reach"], 0o377)
        self.assertEqual(s[1]["reach"], 0o37)
        st = chrony.parse_sourcestats("PPS,13,7,194,-0.001,0.064,-0.000000005,0.000002914\n10.0.0.1,3,1,60,nan,nan,nan,nan\n")
        merged = chrony.merge_sources(s, st)
        self.assertEqual(merged[0]["np"], 13)
        self.assertIsNone(merged[1]["std_dev_s"])

    def test_serverstats_variable_length(self):
        self.assertEqual(chrony.parse_serverstats("100,2,3,0,0\n")["ntp_rx"], 100)
        self.assertEqual(chrony.parse_serverstats("100,2,3,0,0,1,1,5,0,0,0\n")["nts_auth"], 5)


class Ntp(unittest.TestCase):
    def test_timestamp_roundtrip(self):
        for ns in (0, 1_700_000_000_123_456_789, 2_100_000_000_000_000_000):  # last one is after 2036
            self.assertLessEqual(abs(ntpprobe.ntp64_to_ns(ntpprobe.ns_to_ntp64(ns)) - ns), 1)

    def test_probe_against_fake_server(self):
        sock = fakes.start_ntp(12999, offset_ns=5_000_000)
        try:
            r = ntpprobe.query_once("127.0.0.1", 12999, 2.0)
        finally:
            sock.close()
        self.assertEqual(r["stratum"], 1)
        self.assertEqual(r["ref_id"], "PPS")
        self.assertAlmostEqual(r["offset_s"], 0.005, delta=0.001)
        self.assertGreater(r["delay_s"], 0)
        self.assertTrue(r["synced"])

    def test_origin_mismatch(self):
        pkt, t1 = ntpprobe.build_request(time.time_ns())
        with self.assertRaises(ntpprobe.NtpError):
            ntpprobe.parse_response(bytes([0x24]) + pkt[1:], t1 ^ 1, 0, 0, 0)


class Sats(unittest.TestCase):
    def test_zenith_is_below_receiver(self):
        lat, lon = sats.sub_satellite_point(40.0, -75.0, 123.0, 90.0, 20200.0)
        self.assertAlmostEqual(lat, 40.0, places=4)
        self.assertAlmostEqual(lon, -75.0, places=4)

    def test_horizon_distance(self):
        lat, lon = sats.sub_satellite_point(0.0, 0.0, 0.0, 0.0, 20200.0)  # due north on the horizon
        self.assertAlmostEqual(lat, math.degrees(math.acos(6371 / (6371 + 20200))), places=3)
        self.assertAlmostEqual(lon, 0.0, places=4)

    def test_naming(self):
        self.assertEqual(sats.normalize_sat({"PRN": 78, "az": 1, "el": 2})["id"], "R14")
        self.assertEqual(sats.normalize_sat({"PRN": 7, "gnssid": 0, "svid": 7})["gnss"], "GPS")
        self.assertEqual(sats.normalize_sat({"PRN": 311})["gnss"], "Galileo")


class Store_(unittest.TestCase):
    def test_history_and_events(self):
        s = Store(":memory:", 1) if False else Store(os.path.join("/tmp", f"t{time.time_ns()}", "x.sqlite"), 1)
        now = time.time()
        for i in range(10):
            s.add_samples(now - 100 + i * 10, {"a": float(i), "b": None, "c": float("nan")})
        h = s.history(["a", "b"], now - 200, now, max_points=5)
        self.assertGreaterEqual(len(h["a"]), 2)
        self.assertEqual(h["b"], [])
        s.add_event("info", "x", "hello")
        self.assertEqual(s.events()[0]["message"], "hello")


def _srv(sources, tr=TRACKING, kind="chrony", ok=True):
    t = backends.normalize_chrony_tracking(chrony.parse_tracking(tr)) if tr else None
    return {"kind": kind, "ok": ok, "error": None if ok else "boom", "tracking": t, "sources": sources}


GPS_OK = {"connected": True, "tpv": {"mode": 3}, "tpv_age_s": 1, "sats": [{"used": True}] * 8}
PROBE_OK = {"samples": 5, "ok": True, "loss_pct": 0, "port": 123}
PPS = {"mode": "#", "state": "*", "name": "PPS", "reach": 0o377}
NET_TRACKING = "A29FC801,time.cloudflare.com,3,1794786858.1,0.00004,0.0003,0.0004,-3.9,0.01,0.2,0.018,0.0012,64.0,Normal"


def net(name, state="+", reach=0o377):
    return {"mode": "^", "state": state, "name": name, "reach": reach}


class Assess(unittest.TestCase):
    def test_gps_locked(self):
        a = assess(_srv([PPS]), GPS_OK, PROBE_OK)
        self.assertEqual((a["code"], a["level"]), ("gps_locked", "ok"))

    def test_no_fix_is_holdover(self):
        a = assess(_srv([PPS]), dict(GPS_OK, tpv={"mode": 1}), PROBE_OK)
        self.assertEqual((a["code"], a["level"]), ("gps_holdover", "warn"))

    def test_gps_server_using_network_is_a_warning(self):
        a = assess(_srv([dict(PPS, state="-"), net("10.0.0.1", "*")]), GPS_OK, PROBE_OK)
        self.assertEqual((a["code"], a["level"]), ("network_sync", "warn"))

    def test_network_server_without_gps_is_healthy(self):
        srcs = [net("a", "*"), net("b"), net("c"), net("d")]
        a = assess(_srv(srcs, NET_TRACKING), None, PROBE_OK)
        self.assertEqual((a["code"], a["level"]), ("network_synced", "ok"))
        self.assertEqual(a["attention"], 0)
        self.assertEqual(a["usable_sources"], 4)
        self.assertNotIn("gps", [c["id"] for c in a["checks"]])
        self.assertNotIn("pps", [c["id"] for c in a["checks"]])

    def test_few_upstream_sources_warn(self):
        a = assess(_srv([net("a", "*"), net("b")], NET_TRACKING), None, PROBE_OK)
        up = next(c for c in a["checks"] if c["id"] == "upstream")
        self.assertEqual(up["state"], "warn")
        self.assertEqual(a["level"], "warn")

    def test_falseticker_is_flagged(self):
        a = assess(_srv([net("a", "*"), net("b"), net("c"), net("bad", "x")], NET_TRACKING), None, PROBE_OK)
        up = next(c for c in a["checks"] if c["id"] == "upstream")
        self.assertEqual(up["state"], "warn")
        self.assertIn("flagged", up["detail"])

    def test_chrony_down_explicit(self):
        srv = {"kind": None, "ok": False, "error": "timeout", "tracking": None, "sources": []}
        a = assess(srv, None, PROBE_OK, server_type="chrony")
        self.assertEqual((a["code"], a["level"]), ("daemon_down", "bad"))

    def test_basic_view_from_probe_only(self):
        pt = backends.probe_tracking({"server": {"stratum": 2, "ref_id": "162.159.200.1", "leap": 0, "synced": True,
                                                 "root_delay_s": 0.018, "root_dispersion_s": 0.002}})
        srv = {"kind": None, "ok": False, "error": "x", "tracking": pt, "sources": []}
        a = assess(srv, None, PROBE_OK, server_type="auto")
        self.assertEqual(a["code"], "network_synced")
        self.assertEqual(next(c for c in a["checks"] if c["id"] == "daemon")["state"], "warn")
        self.assertEqual(assess(srv, None, PROBE_OK, server_type="probe")["attention"], 0)

    def test_basic_view_stratum1_counts_as_reference_clock(self):
        pt = backends.probe_tracking({"server": {"stratum": 1, "ref_id": "GPS", "leap": 0, "synced": True,
                                                 "root_delay_s": 0.0, "root_dispersion_s": 0.0001}})
        a = assess({"kind": None, "ok": False, "error": None, "tracking": pt, "sources": []}, None, PROBE_OK, server_type="probe")
        self.assertEqual(a["code"], "gps_locked")
        self.assertEqual(a["title"], "Locked to a reference clock")

    def test_no_answer(self):
        srv = {"kind": None, "ok": False, "error": None, "tracking": None, "sources": []}
        a = assess(srv, None, {"samples": 5, "ok": False, "loss_pct": 100, "port": 123, "error": "timeout"}, server_type="probe")
        self.assertEqual(a["code"], "no_answer")

    def test_unsynchronised(self):
        tr = "00000000,,0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,Not synchronised"
        self.assertEqual(assess(_srv([], tr), GPS_OK, PROBE_OK)["code"], "unsynchronised")

    def test_public_servers_agreement(self):
        srcs = [net("a", "*"), net("b"), net("c")]
        ok = assess(_srv(srcs, NET_TRACKING), None, PROBE_OK, refs=[{"diff_s": 0.0012}, {"diff_s": -0.0008}])
        self.assertEqual(next(c for c in ok["checks"] if c["id"] == "public")["state"], "ok")
        off = assess(_srv(srcs, NET_TRACKING), None, PROBE_OK, refs=[{"diff_s": 0.35}, {"diff_s": 0.34}])
        self.assertEqual(next(c for c in off["checks"] if c["id"] == "public")["state"], "bad")
        wait = assess(_srv(srcs, NET_TRACKING), None, PROBE_OK, refs=[{"diff_s": None}])
        self.assertEqual(next(c for c in wait["checks"] if c["id"] == "public")["state"], "unknown")

    def test_median_ref_diff_ignores_one_bad_public_server(self):
        self.assertAlmostEqual(median_ref_diff([{"diff_s": 0.001}, {"diff_s": 0.002}, {"diff_s": 5.0}]), 0.002)

    def test_find_refclock_prefers_selected(self):
        self.assertEqual(find_refclock([{"mode": "#", "state": "-", "name": "NMEA"}, PPS])["name"], "PPS")


class Ntpd(unittest.TestCase):
    def test_parse_vars_with_quotes_and_commas(self):
        v = ntpq.parse_vars('version="ntpd 4.2.8, x", leap=00, stratum=2,\r\n offset=-0.412')
        self.assertEqual(v["version"], "ntpd 4.2.8, x")
        self.assertEqual(v["stratum"], "2")
        self.assertEqual(v["offset"], "-0.412")

    def test_leap_formats(self):
        self.assertEqual(ntpq.parse_leap("00", 0), 0)
        self.assertEqual(ntpq.parse_leap("11", 0), 3)
        self.assertEqual(ntpq.parse_leap("01", 0), 1)
        self.assertEqual(ntpq.parse_leap(None, 0xC000), 3)

    def test_reach_formats(self):
        self.assertEqual(ntpq.parse_reach("377"), 0o377)
        self.assertEqual(ntpq.parse_reach("37"), 0o37)
        self.assertEqual(ntpq.parse_reach("ff"), 255)
        self.assertEqual(ntpq.parse_reach("0x3f"), 63)
        self.assertEqual(ntpq.parse_reach(""), 0)

    def test_reftime(self):
        self.assertAlmostEqual(ntpq.ntp_hex_to_unix("0xe9a3a3a0.80000000"), 0xe9a3a3a0 - 2208988800 + 0.5)
        self.assertIsNone(ntpq.ntp_hex_to_unix("0x00000000.00000000"))

    def test_snapshot_against_fake_ntpd(self):
        sock = fakes.start_ntp(12988, control=True, stratum=2)
        try:
            snap = ntpq.snapshot("127.0.0.1", 12988, timeout=2.0)
        finally:
            sock.close()
        tr = snap["tracking"]
        self.assertEqual((tr["stratum"], tr["synced"], tr["leap_status"]), (2, True, "Normal"))
        self.assertAlmostEqual(tr["last_offset_s"], -0.412e-3)
        self.assertAlmostEqual(tr["root_dispersion_s"], 31.207e-3)
        self.assertAlmostEqual(tr["freq_ppm"], -12.345)
        self.assertEqual(tr["ref_name"], "162.159.200.1")
        src = snap["sources"]
        self.assertEqual(len(src), 4)  # fragments were delivered out of order and reassembled
        self.assertEqual([s["state"] for s in src], ["*", "+", "+", "-"])
        self.assertEqual(src[2]["reach"], 0o367)
        self.assertEqual(src[2]["poll"], 7)
        self.assertAlmostEqual(src[1]["std_dev_s"], 0.402e-3)
        self.assertTrue(all(s["mode"] == "^" for s in src))

    def test_ntpd_that_ignores_control_queries(self):
        sock = fakes.start_ntp(12987, control=False)
        try:
            with self.assertRaises(ntpq.NtpqError) as cm:
                ntpq.snapshot("127.0.0.1", 12987, timeout=0.4)
        finally:
            sock.close()
        self.assertIn("timeout", str(cm.exception))

    def test_refclock_peer_row(self):
        row = ntpq.source_from_vars(1, 0x9614 | (6 << 8) & 0xFFFF, {
            "srcadr": "127.127.22.0", "refid": ".PPS.", "stratum": "0", "hpoll": "4", "reach": "377",
            "offset": "0.001", "jitter": "0.002", "delay": "0"})
        self.assertEqual((row["mode"], row["state"], row["name"]), ("#", "*", "PPS (127.127.22.0)"))


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self._old = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._old)

    def test_defaults_and_alias(self):
        for k in ("SERVER_HOST", "CHRONY_HOST", "REFERENCE_SERVERS", "SERVER_TYPE", "GPSD_ENABLED"):
            os.environ.pop(k, None)
        os.environ["CHRONY_HOST"] = "10.1.1.5"
        c = Config.from_env()
        self.assertEqual((c.server_host, c.gpsd_host, c.ntp_host, c.server_type, c.gpsd_mode),
                         ("10.1.1.5", "10.1.1.5", "10.1.1.5", "auto", "auto"))
        self.assertEqual(c.reference_servers, ("time.cloudflare.com", "time.google.com"))

    def test_empty_reference_list_switches_it_off(self):
        os.environ["REFERENCE_SERVERS"] = ""
        self.assertEqual(Config.from_env().reference_servers, ())
        os.environ["REFERENCE_SERVERS"] = "a.example, b.example:1123  c.example"
        self.assertEqual(Config.from_env().reference_servers, ("a.example", "b.example:1123", "c.example"))

    def test_gpsd_legacy_values(self):
        os.environ["GPSD_ENABLED"] = "false"
        self.assertEqual(Config.from_env().gpsd_mode, "false")
        os.environ["GPSD_ENABLED"] = "yes"
        self.assertEqual(Config.from_env().gpsd_mode, "true")

    def test_bad_server_type_is_rejected(self):
        os.environ["SERVER_TYPE"] = "windows"
        with self.assertRaises(SystemExit):
            Config.from_env()

    def test_split_hostport(self):
        self.assertEqual(ntpprobe.split_hostport("pool.ntp.org"), ("pool.ntp.org", 123))
        self.assertEqual(ntpprobe.split_hostport("10.0.0.1:1123"), ("10.0.0.1", 1123))
        self.assertEqual(ntpprobe.split_hostport("[::1]:1123"), ("::1", 1123))
        self.assertEqual(ntpprobe.split_hostport("fe80::1"), ("fe80::1", 123))


if __name__ == "__main__":
    unittest.main()
