# Live read-only inventory — 2026-09-08

TrueNAS **25.10.7**, Linux 6.12.105-production+truenas; AMD Ryzen 7 5825U,
16 logical CPUs. `/mnt/data/store` is dataset `data/store`, recordsize 128K,
9.62 TiB available at observation. Downloads/films/serials report same filesystem.
Actual hardlink and container UID/ACL access still require separate verification.
POSIX mode of store: root:root 0770; do not infer container access from mode alone.

Observed containers:

| Application | Image version | State |
|---|---|---|
| Seerr | v3.4.1 | running, unhealthy |
| Jellyfin | 10.11.11 | healthy |
| qBittorrent | 5.2.3 | healthy |
| Prowlarr | 2.6.3.5592 | healthy |
| Radarr | 6.4.3.10645 | healthy |
| Sonarr | 4.0.19.3011 | healthy |
| FlareSolverr | v3.5.0 | healthy |

Ports occupied: 22,53,80,139,443,445,5357,6000,6999,11180,30013,30014,
30021,30024,30025,30041,30050,30098,30113,30357,30363,30364,31054,31055,45000.
Port 30280 was not listening at observation; recheck before deploy.
No existing service stopped, no production settings changed during inventory.
Local development has uv/Python 3.12/node 22/ffmpeg, no Docker or .NET command.
