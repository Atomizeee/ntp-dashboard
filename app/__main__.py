import logging
import signal
import threading

from . import __version__
from .config import Config
from .monitor import Monitor
from .server import make_server


def main() -> None:
    cfg = Config.from_env()
    logging.basicConfig(level=logging.DEBUG if cfg.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("main")
    log.info("ntp-dashboard %s: server=%s type=%s gpsd=%s ntp-port=%d reference-servers=%s",
             __version__, cfg.server_host, cfg.server_type,
             f"{cfg.gpsd_host}:{cfg.gpsd_port} ({cfg.gpsd_mode})" if cfg.gpsd_mode != "false" else "off",
             cfg.ntp_port, ",".join(cfg.reference_servers) or "none")
    monitor = Monitor(cfg)
    monitor.start()
    srv = make_server(monitor, cfg)

    def shutdown(*_):
        threading.Thread(target=srv.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    log.info("listening on http://%s:%d", cfg.listen_host, cfg.listen_port)
    srv.serve_forever()
    monitor.stop()


if __name__ == "__main__":
    main()
