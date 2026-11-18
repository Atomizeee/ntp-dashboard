# Security policy

## Reporting a vulnerability

Please report security problems privately using GitHub's "Report a vulnerability" button on the Security tab of this repository, and do not open a public issue. Include the version, how you run it, and steps to reproduce. Expect a first reply within a few days.

## Deployment notes

- The dashboard is read-only toward your NTP server and gpsd, but it exposes server details and, with GPS, an approximate location. Keep it on a trusted network or behind TLS and `BASIC_AUTH_*`.
- Restrict chrony's command port, ntpd status queries and gpsd to the dashboard host.
- Use `HIDE_POSITION=true` and a low `COORD_DECIMALS` for anything public.
