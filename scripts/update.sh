#!/usr/bin/env bash
set -euo pipefail

APP_ID=4564210
SERVER_INSTALL_DIR=/data/server
STEAM_PLATFORM_TYPE=windows

STEAM_USERNAME="${STEAM_USERNAME:-}"
STEAM_PASSWORD="${STEAM_PASSWORD:-}"
STEAM_AUTH_CODE="${STEAM_AUTH_CODE:-}"
STEAM_VALIDATE="${STEAM_VALIDATE:-false}"

mkdir -p "${SERVER_INSTALL_DIR}"

STEAMCMD_BIN="$(command -v steamcmd || true)"
if [[ -z "${STEAMCMD_BIN}" ]]; then
  echo "ERROR: steamcmd not found in container." >&2
  exit 1
fi

if [[ -z "${STEAM_USERNAME}" || -z "${STEAM_PASSWORD}" ]]; then
  echo "ERROR: STEAM_USERNAME and STEAM_PASSWORD are required." >&2
  echo "Use your Steam account name (not email) for STEAM_USERNAME." >&2
  exit 2
fi

print_hints() {
  local exit_code="$1"

  case "${exit_code}" in
    5)
      echo "Hint: Login denied (password or Steam Guard issue)." >&2
      echo "Use Steam account name (not email) and a fresh STEAM_AUTH_CODE." >&2
      ;;
    8)
      echo "Hint: Missing entitlement/subscription for app ${APP_ID}." >&2
      echo "Owning app 3058630 does not always include dedicated server app ${APP_ID}." >&2
      ;;
  esac
}

fail_with_hints() {
  local exit_code="$1"
  local message="$2"
  echo "ERROR: ${message} (exit code ${exit_code})." >&2
  print_hints "${exit_code}"
  exit "${exit_code}"
}

declare -a app_update_args=(+app_update "${APP_ID}")
if [[ "${STEAM_VALIDATE,,}" == "true" ]]; then
  app_update_args=(+app_update "${APP_ID}" validate)
fi

declare -a steamcmd_args
steamcmd_args=(
  +@sSteamCmdForcePlatformType "${STEAM_PLATFORM_TYPE}"
  +force_install_dir "${SERVER_INSTALL_DIR}"
  +login "${STEAM_USERNAME}" "${STEAM_PASSWORD}"
)
if [[ -n "${STEAM_AUTH_CODE}" ]]; then
  steamcmd_args+=("${STEAM_AUTH_CODE}")
fi
steamcmd_args+=("${app_update_args[@]}" +quit)

echo "SteamCMD login/update phase ..."
set +e
"${STEAMCMD_BIN}" "${steamcmd_args[@]}"
steamcmd_exit=$?
set -e
if [[ "${steamcmd_exit}" -ne 0 ]]; then
  fail_with_hints "${steamcmd_exit}" "SteamCMD login/update failed"
fi
