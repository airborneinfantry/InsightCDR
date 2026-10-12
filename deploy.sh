#!/usr/bin/env bash
# Deploys the insightcdr tree to llmdev:/opt/insightcdr and restarts units.
# Run from the repo root (any host with rsync + the llmadmin ssh key):
#   bash insightcdr/deploy.sh
#
# Notes on the brief-verbatim script (documented deviations):
# - No venv: /opt/insightcdr runs on system python3 + user site-packages, so
#   the units exec /usr/bin/python3 -m uvicorn|ingest (no venv exclude needed,
#   kept anyway for safety).
# - rsync --delete must NOT wipe server-only state, so config/ (the .env),
#   data/ (backups) and spool/ are excluded from BOTH rsync passes.
# - The Caddy :8446 site block is appended once, with an idempotence check,
#   and caddy is restarted (reload is refused on this host: `admin off`).
set -euo pipefail
KEY=~/.ssh/id_ed25519_deskbuild
HOST="llmadmin@192.168.0.123"

rsync -a --delete --exclude venv --exclude __pycache__ \
      --exclude config --exclude data --exclude spool \
      -e "ssh -i $KEY -o BatchMode=yes" \
      "insightcdr/" "$HOST:/tmp/insightcdr-tree/"

ssh -i "$KEY" -o BatchMode=yes "$HOST" '
  sudo rsync -a --delete --exclude config --exclude data --exclude spool \
    /tmp/insightcdr-tree/ /opt/insightcdr/ &&
  sudo install -m 644 /opt/insightcdr/deploy/insightcdr-web.service /etc/systemd/system/ &&
  sudo install -m 644 /opt/insightcdr/deploy/insightcdr-ingest.service /etc/systemd/system/ &&
  sudo install -m 644 /opt/insightcdr/deploy/insightcdr-backup.service /etc/systemd/system/ &&
  sudo install -m 644 /opt/insightcdr/deploy/insightcdr-backup.timer /etc/systemd/system/ &&
  sudo systemctl daemon-reload &&
  sudo systemctl enable --now insightcdr-web insightcdr-ingest insightcdr-backup.timer &&
  sudo systemctl restart insightcdr-web insightcdr-ingest
  # Caddy :8446 site block — appended ONCE (idempotent), then restart.
  # The block lives in /etc/caddy/insightcdr.caddyfile (imported from the main
  # Caddyfile), so the idempotence check greps for the import line itself,
  # not for ":8446" (which only appears in the imported file).
  if ! sudo grep -q "import /etc/caddy/insightcdr.caddyfile" /etc/caddy/Caddyfile; then
    sudo cp /opt/insightcdr/deploy/Caddyfile.insightcdr /etc/caddy/insightcdr.caddyfile
    echo "import /etc/caddy/insightcdr.caddyfile" | sudo tee -a /etc/caddy/Caddyfile >/dev/null
  fi
  # restart, not reload: the shared Caddyfile sets `admin off`, so the reload
  # API on :2019 refuses and `systemctl reload caddy` is a no-op that exits
  # non-zero. Restart applies the config.
  sudo systemctl restart caddy'
