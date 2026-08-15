#!/usr/bin/env bash
# Container entrypoint. The dashboard is the long-lived main process: it stays up even when
# the AC EVO server binary crashes, and it starts/stops/restarts the server (scripts/run_server.sh).
set -euo pipefail

PUID="${PUID:-0}"
PGID="${PGID:-0}"
AUTO_UPDATE="${AUTO_UPDATE:-true}"

run_server_update() {
  /opt/acevo/scripts/update.sh
}

run_server_update_if_enabled() {
  local update_exit

  if [[ "${AUTO_UPDATE,,}" != "true" ]]; then
    return 0
  fi

  echo "Checking for AC EVO server updates before dashboard startup..."
  if run_server_update; then
    echo "Steam update check finished."
  else
    update_exit=$?
    echo "WARNING: Steam update failed with exit code ${update_exit}; starting dashboard with existing server files." >&2
  fi
}

prepare_mod_storage() {
  python3 -m dashboard.mods
}

main() {
  if [[ "${PUID}" -ne 0 && "$(id -u)" -eq 0 ]]; then
    echo "Switching to user PUID=${PUID} and PGID=${PGID}..."

    if ! getent group "${PGID}" > /dev/null; then
      groupadd -g "${PGID}" acevo
    fi
    if ! getent passwd "${PUID}" > /dev/null; then
      useradd -u "${PUID}" -g "${PGID}" -d /root -M -s /bin/bash acevo
    fi

    TARGET_USER="$(getent passwd "${PUID}" | cut -d: -f1)"
    TARGET_GROUP="$(getent group "${PGID}" | cut -d: -f1)"

    # Adjust ownership for required directories
    chown -R "${TARGET_USER}:${TARGET_GROUP}" /data /root /opt/acevo 2>/dev/null || true

    export HOME=/root
    export USER="${TARGET_USER}"
    exec gosu "${TARGET_USER}" "$0" "$@"
  fi

  mkdir -p /data
  run_server_update_if_enabled
  prepare_mod_storage
  echo "Starting AC EVO dashboard (container main process) on port ${DASHBOARD_PORT:-8090} ..."
  exec python3 -m dashboard
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
