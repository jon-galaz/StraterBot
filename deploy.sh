#!/usr/bin/env bash
# Deploy trading system to VPS.
# Run once on the server after cloning the repo and creating .env
set -euo pipefail

echo "==> Pulling latest changes"
git pull

echo "==> Building image"
docker compose build --no-cache

echo "==> Restarting service"
docker compose down --remove-orphans
docker compose up -d

echo "==> Logs (Ctrl+C to stop watching)"
docker compose logs -f
