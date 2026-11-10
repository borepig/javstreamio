#!/usr/bin/env bash
# Re-deploy: pull the latest main, rebuild the Next app, restart pm2 + reload nginx.
# Run this on the EC2 after each `git push` so the live addon picks up new metas/streams.
set -euo pipefail
DOMAIN="${DOMAIN:-}"

cd /opt/javstreamio
# The root user-data bootstrap (and any root `sudo git` runs) can leave
# root-owned .git/objects, which makes the ubuntu user's `git pull` fail with
# "insufficient permission for adding an object". Re-chown .git to ubuntu.
sudo chown -R ubuntu:ubuntu .git 2>/dev/null || true
git pull --ff-only origin main
cd debrid-app

# The t3.micro (1GB RAM) OOMs during `npm ci`. Ensure a 2G swapfile exists so
# installs and builds have headroom (persisted in /etc/fstab).
if ! swapon --show | grep -q /swapfile; then
  sudo fallocate -l 2G /swapfile 2>/dev/null || sudo dd if=/dev/zero of=/swapfile bs=1M count=2048
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  grep -q swapfile /etc/fstab || echo "/swapfile none swap sw 0 0" | sudo tee -a /etc/fstab
fi

# Stop the running app first: it holds node_modules/.bin open, so wiping +
# reinstalling while it runs trips EACCES. `pm2 stop` kills the `next start`
# parent, but the `next-server` child can linger and keep .bin open, so settle
# briefly and kill any surviving child before the wipe. `pm2 stop` may fail if
# the app definition is gone (rebuilds can drop it) — tolerate that.
sudo pm2 stop javstreamio-debrid 2>/dev/null || true
sleep 2
sudo pkill -f "next-server" 2>/dev/null || true

# node_modules is root-owned (created by the root user-data bootstrap), so the
# install + build run as root. Wipe first so an interrupted/OOM-killed install
# (mixed ownership) can't trip `npm ci`. (Plain `sudo npm` is what worked; the
# app is already stopped above, so the .bin lock is released.)
sudo rm -rf node_modules
sudo npm ci
sudo npm run build

# The debrid-app is registered under the root pm2 daemon, not the ubuntu one.
# A rebuild can drop or break the pm2 app definition ("Script not found"), so
# re-register from the committed ecosystem file: delete any stale entry (tolerating
# "not found"), then start from aws/pm2.ecosystem.json so the app comes back up
# cleanly after the build.
sudo pm2 delete javstreamio-debrid 2>/dev/null || true
sudo pm2 start aws/pm2.ecosystem.json
sudo pm2 save
sudo systemctl reload nginx

if [ -n "$DOMAIN" ]; then
  echo "redeployed: https://${DOMAIN}/manifest.json (version 0.5.10)"
else
  echo "redeployed: http://<public-ip>/manifest.json (version 0.5.10)"
fi
