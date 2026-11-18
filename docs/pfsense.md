# pfSense and OPNsense (ntpd)

Both run ntpd. The dashboard reads it through the standard NTP control protocol (mode 6, the same data `ntpq` shows). Without any change you still get the basic view from the normal NTP port.

## Unlock the full view

ntpd must allow status queries from the dashboard host. Default pfSense and OPNsense configurations restrict this with `noquery`.

- **pfSense**: Services > NTP > ACLs. Add a network entry for the dashboard host (or its subnet) and untick **Disable all NTP queries / "noquery"**. Save.
- **OPNsense**: Services > Network Time > General, advanced options. Make sure the generated `restrict` line for the dashboard's network doesn't include `noquery`.

The equivalent `ntp.conf` line is:

```
restrict 192.168.1.20 nomodify nopeer notrap
```

(no `noquery`). Menu names vary between versions, so if you can't find the option, open the generated `ntp.conf` and check the `restrict` lines.

Also make sure the firewall rule on the interface lets the dashboard host reach **UDP 123** on the firewall.

## Check

```bash
docker compose run --rm ntp-dashboard python -m app.selftest
```

`/api/raw` on the dashboard shows exactly what ntpd returned. Set `SERVER_TYPE=ntpd` to skip auto-detection.

## Notes

- NTP clients served is not available for ntpd.
- A reference clock such as a GPS on the firewall appears in the sources table as `GPS` or `PPS` with a `#` marker.
