#!/usr/bin/env bash
# Re-deploy: pull the latest main, rebuild the Next app, restart pm2 + reload nginx.
# Run this on the EC2 after each `git push` so the live addon picks up new metas/streams.
set -euo pipefail
DOMAIN="${DOMAIN:-}"

cd /opt/javstreamio
git pull --ff-only origin main
cd debrid-app
npm ci
npm run build
# The debrid-app is registered under the root pm2 daemon, not the ubuntu one.
sudo pm2 restart javstreamio-debrid
sudo pm2 save
systemctl reload nginx

echo "redeployed: http://${DOMAIN:-<public-ip>}/manifest.json (version 0.5.8)"
