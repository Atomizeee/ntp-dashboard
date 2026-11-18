# Basic mode and public servers

Basic mode needs nothing from the server except a working NTP port, so it also works with routers, appliances and public pools.

You get: stratum, reference ID, root delay and dispersion, the error bound the server announces, round-trip time, jitter, packet loss, and the offset between the server and the dashboard host.

## Limits of the basic view

- Offsets are measured over the network, so they carry the noise of the path. Don't read microseconds into them.
- No source table, no frequency data, no request statistics. Use chrony or ntpd access for those.
- The offset is relative to the machine running the dashboard, so keep that machine's clock synced too.

## Monitoring a public pool

Set `SERVER_HOST=pool.ntp.org` and `SERVER_TYPE=probe`. A pool name returns a different server on each lookup, so for steady numbers monitor one fixed server, for example `time.cloudflare.com`.

Be polite: public servers are shared. Use `NTP_INTERVAL=30` or more for servers you don't run, and keep `REFERENCE_INTERVAL` at 64 or more.
