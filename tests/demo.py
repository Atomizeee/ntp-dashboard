"""Run the dashboard against fake servers, no hardware or internet needed.

    python -m tests.demo            # GPS/PPS server (chrony + gpsd)
    python -m tests.demo network    # network-synced chrony server, no GPS
    python -m tests.demo ntpd       # ntpd server (pfSense style), read with control queries
    python -m tests.demo basic      # any NTP server, only the NTP port readable
"""
import os
import sys
import tempfile

from . import fakes

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    scenario = sys.argv[1] if len(sys.argv) > 1 else "gps"
    if scenario not in ("gps", "network", "ntpd", "basic"):
        raise SystemExit(__doc__)
    port = int(os.environ.get("PORT", "8080"))
    env = dict(
        CHRONYC_BIN=os.path.join(HERE, "fake_chronyc"), SERVER_HOST="127.0.0.1",
        GPSD_HOST="127.0.0.1", GPSD_PORT="12947", NTP_HOST="127.0.0.1", NTP_PORT="12123",
        DATA_DIR=os.environ.get("DATA_DIR", tempfile.mkdtemp(prefix="ntp-demo-")),
        SAMPLE_INTERVAL=os.environ.get("SAMPLE_INTERVAL", "2"), PORT=str(port), COORD_DECIMALS="2",
        FAKE_CHRONY_SCENARIO="gps", REFERENCE_INTERVAL="32",
        REFERENCE_SERVERS="127.0.0.1:12201,127.0.0.1:12202",
    )
    # two fake "public" servers, slightly ahead and behind, with internet-like delay
    fakes.start_ntp(12201, offset_ns=1_300_000, delay_s=0.012, stratum=1, refid=b"GOOG")
    fakes.start_ntp(12202, offset_ns=-700_000, delay_s=0.018, stratum=2, refid=b"\xa2\x9f\xc8\x01")
    if scenario == "gps":
        env["SERVER_NAME"] = "Rack GPS clock"
        fakes.start_gpsd(12947)
        fakes.start_ntp(12123)
    elif scenario == "network":
        env.update(SERVER_NAME="Proxmox NTP (pool)", FAKE_CHRONY_SCENARIO="network", GPSD_PORT="1")
        fakes.start_ntp(12123, offset_ns=150_000, stratum=3, refid=bytes([162, 159, 200, 1]))
    elif scenario == "ntpd":
        env.update(SERVER_NAME="pfSense NTP", CHRONYC_BIN="/nonexistent", GPSD_PORT="1")
        fakes.start_ntp(12123, offset_ns=-412_000, delay_s=0.0004, stratum=2,
                        refid=bytes([162, 159, 200, 1]), control=True)
    else:
        env.update(SERVER_NAME="Some NTP server", SERVER_TYPE="probe", GPSD_PORT="1")
        fakes.start_ntp(12123, offset_ns=-2_100_000, stratum=2, refid=bytes([203, 0, 113, 4]))
    os.environ.update(env)
    from app.__main__ import main as run
    print(f"Demo '{scenario}' running at http://127.0.0.1:{port}  (fake data, Ctrl+C to stop)", file=sys.stderr)
    run()


if __name__ == "__main__":
    main()
