"""Fake gpsd and NTP servers so the dashboard can be tried without hardware."""
import json
import math
import socket
import socketserver
import struct
import threading
import time

from app.ntpprobe import ns_to_ntp64

LAT, LON = 51.4769, -0.0005  # Greenwich

# (gpsd PRN, gnssid, svid, az, el, snr, used)
SATS = [
    (1, 0, 1, 151, 26, 38, True), (2, 0, 2, 128, 44, 44, True), (7, 0, 7, 270, 79, 47, True),
    (8, 0, 8, 48, 63, 46, True), (9, 0, 9, 212, 18, 31, True), (27, 0, 27, 49, 24, 36, True),
    (30, 0, 30, 308, 45, 42, True), (78, 6, 14, 92, 29, 35, True), (79, 6, 15, 22, 61, 43, True),
    (80, 6, 16, 312, 27, 33, True), (81, 6, 17, 121, 61, 44, True), (82, 6, 18, 175, 27, 34, True),
    (88, 6, 24, 39, 33, 37, True), (11, 2, 11, 200, 52, 40, True), (24, 2, 24, 330, 15, 28, False),
    (133, 1, 133, 160, 12, 22, False), (203, 3, 3, 250, 9, 20, False),
]


class _Gpsd(socketserver.BaseRequestHandler):
    def handle(self):
        s = self.request
        send = lambda o: s.sendall((json.dumps(o) + "\n").encode())
        send({"class": "VERSION", "release": "3.25-fake"})
        send({"class": "DEVICES", "devices": [{"class": "DEVICE", "path": "/dev/ttyS0", "driver": "u-blox",
              "subtype": "SW EXT CORE 3.01, HW 00080000", "bps": 38400, "activated": "2026-11-15T21:26:58.000Z"}]})
        try:
            while True:
                now = time.time()
                send({"class": "TPV", "device": "/dev/ttyS0", "mode": 3,
                      "time": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(now)),
                      "lat": LAT, "lon": LON, "altHAE": 61.2, "epx": 2.1, "epy": 2.4, "epv": 4.0})
                sats = [{"PRN": p, "gnssid": g, "svid": v, "az": (az + now / 60) % 360, "el": el,
                         "ss": ss + 2 * math.sin(now / 7 + p), "used": u} for p, g, v, az, el, ss, u in SATS]
                send({"class": "SKY", "device": "/dev/ttyS0", "hdop": 0.7, "vdop": 1.1, "pdop": 1.3,
                      "nSat": len(sats), "uSat": sum(1 for x in sats if x["used"]), "satellites": sats})
                send({"class": "PPS", "device": "/dev/ttyS0", "real_sec": int(now), "real_nsec": 0,
                      "clock_sec": int(now), "clock_nsec": 4800, "precision": -20, "qErr": -3200})
                time.sleep(1)
        except OSError:
            pass


class _Tcp(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def start_gpsd(port):
    srv = _Tcp(("127.0.0.1", port), _Gpsd)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ---- fake ntpd: NTP mode 3/4 answers plus control mode 6 (what `ntpq` uses) ----

# (assoc id, selection code, vars). Selection: 6 = sys.peer, 4 = candidate, 3 = outlier, 1 = falseticker
NTPD_SYSVARS = ('version="ntpd 4.2.8p17@1.4004-o Mon Oct 28 2024", processor="amd64", system="FreeBSD/14.0-CURRENT", '
                'leap=00, stratum=2, precision=-24, rootdelay=18.455, rootdisp=31.207, refid=162.159.200.1, '
                'reftime=0xeb123456.8c000000, clock=0xeb123460.00000000, peer=41312, tc=6, mintc=3, '
                'offset=-0.412, frequency=-12.345, sys_jitter=0.521, clk_jitter=0.311, clk_wander=0.012')
NTPD_PEERS = [
    (41312, 6, 'srcadr=162.159.200.1, srcport=123, leap=00, hmode=3, pmode=4, stratum=3, hpoll=6, ppoll=6, '
              'reach=377, delay=19.215, offset=-0.412, jitter=0.633, refid=10.12.3.4, rootdelay=7.2'),
    (41313, 4, 'srcadr=216.239.35.0, srcport=123, leap=00, hmode=3, pmode=4, stratum=1, hpoll=6, ppoll=6, '
              'reach=377, delay=24.108, offset=0.221, jitter=0.402, refid=GOOG'),
    (41314, 4, 'srcadr=198.51.100.9, srcport=123, leap=00, hmode=3, pmode=4, stratum=2, hpoll=7, ppoll=7, '
              'reach=367, delay=31.774, offset=1.034, jitter=0.912, refid=203.0.113.4'),
    (41315, 3, 'srcadr=203.0.113.77, srcport=123, leap=00, hmode=3, pmode=4, stratum=2, hpoll=6, ppoll=6, '
               'reach=177, delay=48.9, offset=6.82, jitter=2.9, refid=192.0.2.1'),
]


def _mode6_reply(data, frag=120):
    """Build fragmented control replies for a READSTAT/READVAR request."""
    _b0, op, seq, _st, assoc, _off, _cnt = struct.unpack("!BBHHHHH", data[:12])
    opcode = op & 0x1F
    if opcode == 1:
        body = b"".join(struct.pack("!HH", a, 0x8000 | (sel << 8) | 0x14) for a, sel, _v in NTPD_PEERS)
        status = 0
    elif opcode == 2:
        if assoc == 0:
            body, status = NTPD_SYSVARS.encode(), 0x0644
        else:
            v = next((v for a, _s, v in NTPD_PEERS if a == assoc), None)
            if v is None:
                return [struct.pack("!BBHHHHH", 0x16, 0xC0 | opcode, seq, 5 << 8, assoc, 0, 0)]
            body, status = v.encode(), 0x9614
    else:
        return [struct.pack("!BBHHHHH", 0x16, 0xC0 | opcode, seq, 4 << 8, assoc, 0, 0)]
    out = []
    for off in range(0, max(len(body), 1), frag):
        chunk = body[off:off + frag]
        more = 0x20 if off + frag < len(body) else 0
        hdr = struct.pack("!BBHHHHH", 0x16, 0x80 | more | opcode, seq, status, assoc, off, len(chunk))
        out.append(hdr + chunk + b"\0" * (-len(chunk) % 4))
    return out[::-1]  # deliver fragments out of order on purpose


def start_ntp(port, offset_ns=200_000, delay_s=0.0002, stratum=1, refid=b"PPS\0", control=False):
    """UDP server answering NTP client requests with a clock `offset_ns` ahead.

    With control=True it also answers ntpq-style mode 6 queries like a network-synced ntpd would."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", port))

    def loop():
        while True:
            try:
                data, addr = sock.recvfrom(512)
            except OSError:
                return
            if len(data) >= 12 and (data[0] & 7) == 6:
                if control:
                    for pkt in _mode6_reply(data):
                        sock.sendto(pkt, addr)
                continue
            if len(data) < 48:
                continue
            rx = ns_to_ntp64(time.time_ns() + offset_ns)
            orig = struct.unpack("!Q", data[40:48])[0]
            time.sleep(delay_s)
            tx = ns_to_ntp64(time.time_ns() + offset_ns)
            pkt = struct.pack("!BBbbII4sQQQQ", (0 << 6) | (4 << 3) | 4, stratum, 4, -20, 0, 2000, refid,
                              rx, orig, rx, tx)
            sock.sendto(pkt, addr)

    threading.Thread(target=loop, daemon=True).start()
    return sock
