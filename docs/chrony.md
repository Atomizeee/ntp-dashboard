# chrony (Linux, Proxmox, Raspberry Pi)

The dashboard runs `chronyc` against the server over the network. Remote clients can only run read-only monitoring commands.

## 1. Allow remote monitoring

Add to `/etc/chrony/chrony.conf` (Debian and Proxmox; `/etc/chrony.conf` on RHEL-family systems). Use your own subnet or the dashboard host's IP:

```
bindcmdaddress 0.0.0.0
cmdallow 192.168.1.0/24
```

```bash
systemctl restart chrony
```

## 2. Open the firewall

Allow **UDP 323** from the dashboard host only. Clients that should use this server as their time source also need an `allow` line in `chrony.conf` and UDP 123.

## 3. Check

From the dashboard machine:

```bash
chronyc -n -h <server-ip> tracking
docker compose run --rm ntp-dashboard python -m app.selftest
```

`506 Cannot talk to daemon` means `bindcmdaddress` / `cmdallow` is missing or UDP 323 is blocked.

## Notes

- **NTP clients served** stays empty when the dashboard is on another machine. chrony only answers `serverstats` locally. Use `docker-compose.host.yml` on the server itself to see it.
- A healthy pool-synced server should show at least three reachable upstream servers. Use `pool 2.pool.ntp.org iburst maxsources 4` or several `server` lines.
