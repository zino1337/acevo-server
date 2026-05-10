#!/usr/bin/env bash
set -euo pipefail

PUID="${PUID:-0}"
PGID="${PGID:-0}"

if [[ "${PUID}" -ne 0 && "$(id -u)" -eq 0 ]]; then
  echo "Switching to user PUID=${PUID} and PGID=${PGID}..."

  if ! getent group "${PGID}" >/dev/null; then
    groupadd -g "${PGID}" acevo
  fi
  if ! getent passwd "${PUID}" >/dev/null; then
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

AUTO_UPDATE="${AUTO_UPDATE:-true}"
APP_ID=4564210
SERVER_INSTALL_DIR=/data/server
DEDICATED_EXE_NAME=AssettoCorsaEVOServer.exe
LAUNCHER_EXE_NAME=ServerLauncher.exe
PROTON_BIN=/usr/local/bin/proton
XVFB_BIN=/usr/bin/Xvfb
XVFB_DISPLAY="${XVFB_DISPLAY:-:99}"
XVFB_SERVER_ARGS=(-screen 0 1024x768x24 -nolisten tcp -ac +extension GLX +render)
ACEVO_FORCE_SOFTWARE_RENDERING="${ACEVO_FORCE_SOFTWARE_RENDERING:-true}"
PAYLOAD_GENERATOR=/opt/acevo/scripts/launch_payloads.py
SERVER_PAYLOAD_PATH=/tmp/acevo-serverconfig.b64
SEASON_PAYLOAD_PATH=/tmp/acevo-seasondefinition.b64
PAYLOAD_REPORT_PATH=/tmp/acevo-resolved-env.json
XDG_RUNTIME_DIR=/tmp/acevo-xdg-runtime
STEAM_COMPAT_CLIENT_INSTALL_PATH=/root/.steam/steam
COMPATDATA_ROOT="${SERVER_INSTALL_DIR}/steamapps/compatdata"
STEAM_COMPAT_DATA_PATH="${SERVER_INSTALL_DIR}/steamapps/compatdata/${APP_ID}"
WINEPREFIX="${STEAM_COMPAT_DATA_PATH}/pfx"
MIN_CPU_CORES=2
MIN_MEM_BYTES=$((4 * 1024 * 1024 * 1024))

export STEAM_COMPAT_CLIENT_INSTALL_PATH
export STEAM_COMPAT_DATA_PATH
export WINEPREFIX
export ACEVO_SERVER_INSTALL_DIR="${SERVER_INSTALL_DIR}"
export XDG_RUNTIME_DIR

mkdir -p "${SERVER_INSTALL_DIR}"
mkdir -p "${COMPATDATA_ROOT}"
mkdir -p "${STEAM_COMPAT_DATA_PATH}"
mkdir -p "${WINEPREFIX}"
mkdir -p "${XDG_RUNTIME_DIR}"
chmod 700 "${XDG_RUNTIME_DIR}" 2>/dev/null || true

[[ -x "${PROTON_BIN}" ]] || { echo "ERROR: Proton launcher missing: ${PROTON_BIN}" >&2; exit 1; }
[[ -x "${XVFB_BIN}" ]] || { echo "ERROR: Xvfb missing: ${XVFB_BIN}" >&2; exit 1; }
[[ -f "${PAYLOAD_GENERATOR}" ]] || { echo "ERROR: Launch payload generator missing: ${PAYLOAD_GENERATOR}" >&2; exit 1; }

XVFB_PID=""
RESOURCE_BELOW_MIN=false
RESOURCE_CPU_LIMIT="unknown"
RESOURCE_MEM_MIB="unknown"

is_true() {
  local value="${1:-}"
  case "${value,,}" in
    1 | true | yes | y | on) return 0 ;;
    *) return 1 ;;
  esac
}

print_minimum_requirements_hint() {
  echo "Recommendation: allocate at least ${MIN_CPU_CORES} CPU cores and 4 GiB RAM to the container." >&2
}

cleanup_xvfb() {
  if [[ -n "${XVFB_PID}" ]] && kill -0 "${XVFB_PID}" 2>/dev/null; then
    kill -TERM "${XVFB_PID}" 2>/dev/null || true
    wait "${XVFB_PID}" 2>/dev/null || true
  fi
}

start_xvfb() {
  export DISPLAY="${XVFB_DISPLAY}"
  export SDL_VIDEODRIVER="${SDL_VIDEODRIVER:-x11}"
  if is_true "${ACEVO_FORCE_SOFTWARE_RENDERING}"; then
    export PROTON_USE_WINED3D=1
    export LIBGL_ALWAYS_SOFTWARE=1
    export MESA_LOADER_DRIVER_OVERRIDE=llvmpipe
    export GALLIUM_DRIVER=llvmpipe
  fi
  "${XVFB_BIN}" "${XVFB_DISPLAY}" "${XVFB_SERVER_ARGS[@]}" >/dev/null 2>&1 &
  XVFB_PID=$!
  sleep 1
  if ! kill -0 "${XVFB_PID}" 2>/dev/null; then
    echo "ERROR: Failed to start Xvfb on display ${XVFB_DISPLAY}." >&2
    exit 1
  fi
}

trap cleanup_xvfb EXIT

run_gracefully() {
  local label="$1"
  shift

  "$@" &
  local child_pid=$!
  echo "Started ${label} (pid=${child_pid})"

  trap 'echo "Stopping ${label} (pid=${child_pid})"; kill -TERM "${child_pid}" 2>/dev/null || true' INT TERM

  set +e
  wait "${child_pid}"
  local exit_code=$?
  set -e

  trap - INT TERM
  echo "${label} exited with code ${exit_code}"
  return "${exit_code}"
}

detect_cpu_limit() {
  local host_cpus quota period
  host_cpus="$(getconf _NPROCESSORS_ONLN 2>/dev/null || nproc 2>/dev/null || echo 1)"

  if [[ -r /sys/fs/cgroup/cpu.max ]]; then
    read -r quota period < /sys/fs/cgroup/cpu.max || true
    if [[ -n "${quota:-}" && "${quota}" != "max" && "${period:-0}" =~ ^[0-9]+$ && "${period}" -gt 0 ]]; then
      awk -v q="${quota}" -v p="${period}" 'BEGIN { printf "%.2f", q / p }'
      return
    fi
  fi

  if [[ -r /sys/fs/cgroup/cpu/cpu.cfs_quota_us && -r /sys/fs/cgroup/cpu/cpu.cfs_period_us ]]; then
    quota="$(cat /sys/fs/cgroup/cpu/cpu.cfs_quota_us 2>/dev/null || echo -1)"
    period="$(cat /sys/fs/cgroup/cpu/cpu.cfs_period_us 2>/dev/null || echo 100000)"
    if [[ "${quota}" =~ ^-?[0-9]+$ && "${period}" =~ ^[0-9]+$ && "${quota}" -gt 0 && "${period}" -gt 0 ]]; then
      awk -v q="${quota}" -v p="${period}" 'BEGIN { printf "%.2f", q / p }'
      return
    fi
  fi

  echo "${host_cpus}"
}

detect_mem_limit_bytes() {
  local limit=""

  if [[ -r /sys/fs/cgroup/memory.max ]]; then
    limit="$(tr -d '\n' < /sys/fs/cgroup/memory.max)"
  elif [[ -r /sys/fs/cgroup/memory/memory.limit_in_bytes ]]; then
    limit="$(tr -d '\n' < /sys/fs/cgroup/memory/memory.limit_in_bytes)"
  fi

  if [[ -n "${limit}" && "${limit}" != "max" && "${limit}" =~ ^[0-9]+$ ]]; then
    if awk -v l="${limit}" 'BEGIN { exit !(l > 900000000000000000) }'; then
      limit=""
    fi
  else
    limit=""
  fi

  if [[ -z "${limit}" ]]; then
    limit="$(awk '/MemTotal:/ {printf "%.0f", $2 * 1024; exit}' /proc/meminfo 2>/dev/null || echo 0)"
  fi

  echo "${limit:-0}"
}

report_resource_limits() {
  local cpu_limit mem_bytes mem_mib
  cpu_limit="$(detect_cpu_limit)"
  mem_bytes="$(detect_mem_limit_bytes)"
  mem_mib="$(awk -v b="${mem_bytes}" 'BEGIN { printf "%.0f", b / 1024 / 1024 }')"

  RESOURCE_CPU_LIMIT="${cpu_limit}"
  RESOURCE_MEM_MIB="${mem_mib}"

  echo "Runtime resources detected: cpu_limit=${cpu_limit} mem_limit=${mem_mib} MiB"

  if ! awk -v c="${cpu_limit}" -v min="${MIN_CPU_CORES}" 'BEGIN { exit !(c >= min) }'; then
    RESOURCE_BELOW_MIN=true
  fi
  if [[ "${mem_bytes}" -lt "${MIN_MEM_BYTES}" ]]; then
    RESOURCE_BELOW_MIN=true
  fi

  if [[ "${RESOURCE_BELOW_MIN}" == "true" ]]; then
    echo "WARNING: Resource limits are below recommended minimum for AC EVO." >&2
    print_minimum_requirements_hint
  fi
}

configure_proton_runtime() {
  export WINEDLLOVERRIDES="${WINEDLLOVERRIDES:-winemenubuilder.exe=d}"
  export WINEDEBUG="${WINEDEBUG:--all}"
  export DXVK_LOG_LEVEL="${DXVK_LOG_LEVEL:-none}"
  export VKD3D_DEBUG="${VKD3D_DEBUG:-none}"
}

log_fingerprint() {
  local proton_version
  proton_version="$("${PROTON_BIN}" --version 2>/dev/null | head -n 1 || true)"
  [[ -n "${proton_version}" ]] || proton_version="unknown"

  echo "Runtime fingerprint: proton='${proton_version}' app_id=${APP_ID} install_dir=${SERVER_INSTALL_DIR} display=${XVFB_DISPLAY}"
  echo "Runtime rendering: ACEVO_FORCE_SOFTWARE_RENDERING=${ACEVO_FORCE_SOFTWARE_RENDERING}"
}

run_payload_generator() {
  echo "Generating launch payload blobs from ENV ..."
  set +e
  python3 "${PAYLOAD_GENERATOR}" \
    --server-out "${SERVER_PAYLOAD_PATH}" \
    --season-out "${SEASON_PAYLOAD_PATH}" \
    --report-out "${PAYLOAD_REPORT_PATH}"
  local generator_exit=$?
  set -e

  if [[ "${generator_exit}" -ne 0 ]]; then
    echo "ERROR: Launch payload generator failed with exit code ${generator_exit}." >&2
    exit "${generator_exit}"
  fi
}

bootstrap_wine_prefix_if_needed() {
  if [[ -f "${WINEPREFIX}/system.reg" && -f "${WINEPREFIX}/user.reg" ]]; then
    return 0
  fi

  echo "Detected fresh/partial Wine prefix at ${WINEPREFIX}; bootstrapping via Proton wineboot ..."
  set +e
  "${PROTON_BIN}" run wineboot -u >/tmp/acevo-wineboot.log 2>&1
  local wineboot_exit=$?
  set -e

  if [[ "${wineboot_exit}" -ne 0 ]]; then
    echo "WARNING: wineboot bootstrap exited with code ${wineboot_exit}." >&2
    if [[ -f /tmp/acevo-wineboot.log ]]; then
      tail -n 120 /tmp/acevo-wineboot.log || true
    fi
    return "${wineboot_exit}"
  fi

  echo "Wine prefix bootstrap finished."
  return 0
}

print_crash_hint() {
  local exit_code="$1"
  local label="$2"

  if [[ "${exit_code}" -ne 0 && "${exit_code}" -ne 130 && "${exit_code}" -ne 143 ]]; then
    echo "ERROR: ${label} exited unexpectedly (code ${exit_code})." >&2
    print_minimum_requirements_hint
    echo "Detected resource limits: cpu=${RESOURCE_CPU_LIMIT} mem=${RESOURCE_MEM_MIB} MiB" >&2
  fi
}

if [[ "${AUTO_UPDATE,,}" == "true" ]]; then
  /opt/acevo/scripts/update.sh
fi

configure_proton_runtime
log_fingerprint
report_resource_limits

run_payload_generator

SERVER_PAYLOAD="$(<"${SERVER_PAYLOAD_PATH}")"
SEASON_PAYLOAD="$(<"${SEASON_PAYLOAD_PATH}")"
[[ -n "${SERVER_PAYLOAD}" ]] || { echo "ERROR: Empty server payload." >&2; exit 1; }
[[ -n "${SEASON_PAYLOAD}" ]] || { echo "ERROR: Empty season payload." >&2; exit 1; }

start_xvfb
bootstrap_wine_prefix_if_needed || true

DEDICATED_EXE_PATH="$(find "${SERVER_INSTALL_DIR}" -type f -name "${DEDICATED_EXE_NAME}" | head -n 1 || true)"
if [[ -n "${DEDICATED_EXE_PATH}" ]]; then
  DEDICATED_EXE_DIR="$(dirname "${DEDICATED_EXE_PATH}")"
  chmod +x "${DEDICATED_EXE_PATH}"
  echo "Starting dedicated server with Proton + Xvfb (${XVFB_DISPLAY}): ${DEDICATED_EXE_PATH}"

  set +e
  (
    cd "${DEDICATED_EXE_DIR}"
    run_gracefully "AssettoCorsaEVOServer.exe" \
      "${PROTON_BIN}" runinprefix "./${DEDICATED_EXE_NAME}" \
      -serverconfig "${SERVER_PAYLOAD}" \
      -seasondefinition "${SEASON_PAYLOAD}"
  )
  dedicated_exit=$?
  set -e

  print_crash_hint "${dedicated_exit}" "AssettoCorsaEVOServer.exe"
  exit "${dedicated_exit}"
fi

LAUNCHER_EXE_PATH="$(find "${SERVER_INSTALL_DIR}" -type f -name "${LAUNCHER_EXE_NAME}" | head -n 1 || true)"
if [[ -n "${LAUNCHER_EXE_PATH}" ]]; then
  LAUNCHER_EXE_DIR="$(dirname "${LAUNCHER_EXE_PATH}")"
  chmod +x "${LAUNCHER_EXE_PATH}"
  echo "WARNING: '${DEDICATED_EXE_NAME}' not found. Falling back to '${LAUNCHER_EXE_NAME}'." >&2
  echo "Starting launcher with Proton: ${LAUNCHER_EXE_PATH}"

  set +e
  (
    cd "${LAUNCHER_EXE_DIR}"
    run_gracefully "ServerLauncher.exe" \
      "${PROTON_BIN}" runinprefix "./${LAUNCHER_EXE_NAME}"
  )
  launcher_exit=$?
  set -e

  print_crash_hint "${launcher_exit}" "ServerLauncher.exe"
  exit "${launcher_exit}"
fi

echo "ERROR: Could not find '${DEDICATED_EXE_NAME}' or '${LAUNCHER_EXE_NAME}' in '${SERVER_INSTALL_DIR}'." >&2
exit 1
