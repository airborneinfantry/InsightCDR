#!/usr/bin/env bash
# SFTP chroot for the insightcdr-cdr push user (own spool, distinct from Blue Marmot's).
set -euo pipefail
USER_NAME=insightcdr-cdr
ROOT=/opt/insightcdr/spool
sudo useradd -r -d "$ROOT" -s /usr/sbin/nologin "$USER_NAME" 2>/dev/null || true
sudo mkdir -p "$ROOT/incoming" "$ROOT/quarantine"
sudo chown root:root "$ROOT" && sudo chmod 755 "$ROOT"
sudo chown llmadmin "$ROOT/incoming" "$ROOT/quarantine"
if ! sudo grep -q '^Match User insightcdr-cdr' /etc/ssh/sshd_config; then
  sudo tee -a /etc/ssh/sshd_config >/dev/null <<'SSHEOF'

Match User insightcdr-cdr
    ChrootDirectory /opt/insightcdr/spool
    ForceCommand internal-sftp -d /incoming
    AllowTcpForwarding no
    X11Forwarding no
SSHEOF
  sudo systemctl reload ssh 2>/dev/null || sudo systemctl reload sshd
fi
