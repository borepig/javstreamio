#!/usr/bin/env bash
# EC2 user-data: one-shot bootstrap for the JAVSTREAMIO debrid-app.
# Installs Node 24 + nginx + pm2, clones the repo, builds and starts the
# Next server on :3000 behind nginx. Safe to re-run (idempotent-ish).
set -euo pipefail

APP_DIR=/opt/javstreamio
REPO=https://github.com/borepig/javstreamio.git
PORT=3000
DOMAIN="${DOMAIN:-}"      # optional; if empty, serve on the public IP

# --- system deps ---
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y nginx git curl ca-certificates

# --- Node 24 (matches package.json engines) ---
if ! command -v node >/dev/null 2>&1; then
  curl -fsSL https://deb.nodesource.com/setup_24.x | bash -
  apt-get install -y nodejs
fi
node -v
npm -v

# --- pm2 ---
npm install -g pm2

# --- app: clone + build + start ---
if [ ! -d "$APP_DIR" ]; then
  git clone --depth 1 "$REPO" "$APP_DIR"
fi
cd "$APP_DIR/debrid-app"
npm ci
npm run build

# Use the repo's committed pm2 ecosystem file (carries LULU_COOKIE env);
# the clone above guarantees it exists at $APP_DIR/debrid-app/aws/.
pm2 start "$APP_DIR/debrid-app/aws/pm2.ecosystem.json"
pm2 save

# --- nginx: proxy 80/443 -> :3000 ---
cat > /etc/nginx/sites-available/javstremio.conf <<'NGINX'
server {
  listen 80;
  listen [::]:80;
  server_name ${DOMAIN:-_};

  location / {
    proxy_pass http://127.0.0.1:3000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
    client_max_body_size 20m;
  }
}
NGINX
ln -sf /etc/nginx/sites-available/javstremio.conf /etc/nginx/sites-enabled/javstremio.conf
systemctl enable nginx
systemctl restart nginx

# --- optional TLS via Let's Encrypt (only when a DOMAIN is set) ---
if [ -n "$DOMAIN" ]; then
  apt-get install -y certbot python3-certbot-nginx
  certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "admin@javstremio" --redirect
fi

echo "javstreamio debrid-app is up: http://${DOMAIN:-<public-ip>}/manifest.json"
