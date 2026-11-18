# GPS and PPS with gpsd

GPS panels appear automatically when gpsd answers on `SERVER_HOST:2947` (`GPSD_ENABLED=auto`). Set `GPSD_ENABLED=false` to never show them, or `true` to always show them and report a connection problem.

## Let gpsd listen on the LAN

gpsd listens on localhost only by default. On Debian and Proxmox it is started through `gpsd.socket`, and that socket decides which addresses are open. Create a drop-in file:

```bash
mkdir -p /etc/systemd/system/gpsd.socket.d
cat > /etc/systemd/system/gpsd.socket.d/override.conf <<'EOF'
[Socket]
ListenStream=
ListenStream=/run/gpsd.sock
ListenStream=0.0.0.0:2947
EOF
systemctl daemon-reload
systemctl restart gpsd.socket gpsd
ss -ltnp | grep 2947        # should show 0.0.0.0:2947
```

The empty `ListenStream=` line is required: it clears the default addresses. gpsd has no authentication, so allow **TCP 2947 only from the dashboard host**. Or run the dashboard on the server itself with `docker-compose.host.yml`.

## chrony with GPS and PPS

A typical setup uses the NMEA time from gpsd only for the second number and the PPS pulse for precision:

```
refclock SHM 0 refid NMEA noselect
refclock PPS /dev/pps0 lock NMEA poll 4 filter 16 refid PPS
```

Then `chronyc sources` should show `#* PPS`. The dashboard shows "Locked to GPS" when the selected source is a local reference clock. For a u-blox receiver, enabling more constellations and a stationary dynamic model improves timing; see the vendor's configuration guide.
