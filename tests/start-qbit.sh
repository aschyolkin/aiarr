#!/bin/sh
set -eu
mkdir -p /config/qBittorrent/config
cp /fixtures/qbit-test.conf /config/qBittorrent/config/qBittorrent.conf
exec /app/qbittorrent-nox --profile=/config --webui-port=8080 --confirm-legal-notice
