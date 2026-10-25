#!/usr/bin/env bash

set -Eeuo pipefail

umask 077

readonly DATABASE_NAME="public_restaurant"
readonly BACKUP_DIR="/srv/backups/public-restaurant"
readonly STATE_ROOT="/var/lib/n150-move"
readonly ARMED_LINK="${STATE_ROOT}/armed"
readonly MOVE_NETPLAN="/etc/netplan/99-n150-move-dhcp.yaml"
readonly APP_SERVICE="public-restaurant.service"
readonly WORKER_SERVICE="public-restaurant-worker.service"
readonly PUBLIC_URL="https://gonggibap.com"

STATE_DIR=""
NETPLAN_STAGED=0
DEPART_COMMITTED=0
WORKER_WAS_ACTIVE=0
WEB_WAS_ACTIVE=0

usage() {
  cat <<'EOF'
Usage:
  sudo ./prepare_n150_move.sh preflight
  sudo ./prepare_n150_move.sh depart
  sudo ./prepare_n150_move.sh restore-static
  ./prepare_n150_move.sh postmove
  ./prepare_n150_move.sh router-values

Commands:
  preflight      Read-only checks for services, PostgreSQL and Netplan.
  depart         Stop the worker, create and verify a PostgreSQL backup,
                 stage DHCP for the next boot, stop the web service and power
                 off the N150. Run this only immediately before unplugging it.
  restore-static Restore the pre-move Netplan files for the next boot. This
                 does not apply them to the current connection.
  postmove       Check DHCP, Tailscale, services, DNS and public HTTPS after
                 booting at the new location. It does not print IP addresses.
  router-values  Print the LAN IP, MAC address and public IPv4 in the N150's
                 local terminal for the new router and Hosting.kr settings.

The depart command never runs `netplan apply`. The current connection remains
unchanged until the machine powers off, and DHCP takes effect on the next boot.
EOF
}

log() {
  printf '[n150-move] %s\n' "$*"
}

die() {
  printf '[n150-move] ERROR: %s\n' "$*" >&2
  exit 1
}

require_root() {
  (( EUID == 0 )) || die "run this command with sudo"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

default_interface() {
  local iface
  iface="$(ip -4 route show default | awk 'NR == 1 {print $5}')"
  [[ "$iface" =~ ^[[:alnum:]_.:-]+$ ]] || die "could not identify the default interface"
  [[ -d "/sys/class/net/${iface}" ]] || die "default interface does not exist"
  printf '%s\n' "$iface"
}

print_yes_no() {
  local label="$1"
  shift
  printf '%s=' "$label"
  if "$@" >/dev/null 2>&1; then
    echo yes
  else
    echo no
  fi
}

service_was_active() {
  systemctl is-active --quiet "$1"
}

running_job_count() {
  runuser -u postgres -- psql \
    --dbname="$DATABASE_NAME" \
    --tuples-only \
    --no-align \
    --set=ON_ERROR_STOP=1 \
    --command="SELECT
      (SELECT count(*) FROM operation_jobs WHERE status = 'running')
      +
      (SELECT count(*) FROM oauth_lifecycle_jobs WHERE status = 'running');"
}

check_no_running_jobs() {
  local count
  count="$(running_job_count)" || die "could not inspect operation_jobs"
  [[ "$count" =~ ^[0-9]+$ ]] || die "operation_jobs returned an invalid count"
  (( count == 0 )) || die "${count} worker job(s) are still running"
  log "running operation/OAuth worker jobs: 0"
}

create_verified_backup() {
  local backup_file checksum_file
  install -d -o postgres -g postgres -m 700 "$BACKUP_DIR"
  backup_file="${BACKUP_DIR}/pre-move-$(date +%Y%m%d-%H%M%S).dump"
  checksum_file="${backup_file}.sha256"

  log "creating a consistent PostgreSQL backup"
  runuser -u postgres -- pg_dump \
    --format=custom \
    --dbname="$DATABASE_NAME" \
    --file="$backup_file"
  runuser -u postgres -- pg_restore --list "$backup_file" >/dev/null
  [[ -s "$backup_file" ]] || die "PostgreSQL backup is empty"
  (
    cd "$BACKUP_DIR"
    runuser -u postgres -- sha256sum "$(basename "$backup_file")" \
      >"$checksum_file"
  )
  chown postgres:postgres "$checksum_file"
  chmod 600 "$backup_file" "$checksum_file"
  printf '%s\n' "$backup_file" >"${STATE_DIR}/database-backup"
  log "backup verified with pg_restore and SHA-256"
}

restore_netplan_from_state() {
  local state_dir="$1"
  local manifest="${state_dir}/netplan-manifest"
  local name

  [[ -f "$manifest" ]] || die "Netplan backup manifest is missing"
  rm -f -- "$MOVE_NETPLAN"
  while IFS= read -r name; do
    [[ "$name" =~ ^[[:alnum:]_.-]+\.ya?ml$ ]] \
      || die "unsafe Netplan backup name: $name"
    install -o root -g root -m 600 \
      "${state_dir}/netplan/${name}" "/etc/netplan/${name}"
  done <"$manifest"
  netplan generate
}

stage_dhcp_for_next_boot() {
  local iface="$1"
  local config name temporary
  local -a configs=()

  mapfile -d '' configs < <(
    find /etc/netplan -maxdepth 1 -type f \
      \( -name '*.yaml' -o -name '*.yml' \) -print0 | sort -z
  )
  (( ${#configs[@]} > 0 )) || die "no active Netplan configuration was found"

  install -d -o root -g root -m 700 "${STATE_DIR}/netplan"
  : >"${STATE_DIR}/netplan-manifest"
  for config in "${configs[@]}"; do
    name="$(basename "$config")"
    cp -a -- "$config" "${STATE_DIR}/netplan/${name}"
    printf '%s\n' "$name" >>"${STATE_DIR}/netplan-manifest"
  done

  NETPLAN_STAGED=1
  for config in "${configs[@]}"; do
    rm -f -- "$config"
  done

  temporary="$(mktemp /etc/netplan/.n150-move.XXXXXXXX)"
  cat >"$temporary" <<EOF
network:
  version: 2
  renderer: networkd
  ethernets:
    ${iface}:
      dhcp4: true
      dhcp6: false
      optional: true
EOF
  install -o root -g root -m 600 "$temporary" "$MOVE_NETPLAN"
  rm -f -- "$temporary"
  printf '%s\n' "$iface" >"${STATE_DIR}/interface"

  netplan generate
  ln -s "${STATE_DIR}" "${ARMED_LINK}"
  log "DHCP configuration validated for the next boot"
}

cleanup() {
  local exit_code="$?"
  trap - EXIT INT TERM
  set +e

  if (( exit_code != 0 && NETPLAN_STAGED == 1 && DEPART_COMMITTED == 0 )); then
    log "restoring the original Netplan configuration after failure"
    rm -f -- "$ARMED_LINK"
    restore_netplan_from_state "$STATE_DIR"
  fi
  if (( exit_code != 0 && WORKER_WAS_ACTIVE == 1 && DEPART_COMMITTED == 0 )); then
    systemctl start "$WORKER_SERVICE" >/dev/null 2>&1 || true
  fi
  if (( exit_code != 0 && WEB_WAS_ACTIVE == 1 && DEPART_COMMITTED == 0 )); then
    systemctl start "$APP_SERVICE" >/dev/null 2>&1 || true
  fi
  exit "$exit_code"
}

preflight() {
  local iface
  require_root
  for command_name in awk curl find ip netplan pg_dump pg_restore psql \
    runuser sha256sum ss systemctl; do
    require_command "$command_name"
  done
  iface="$(default_interface)"

  print_yes_no web_active systemctl is-active --quiet "$APP_SERVICE"
  print_yes_no web_enabled systemctl is-enabled --quiet "$APP_SERVICE"
  print_yes_no worker_active systemctl is-active --quiet "$WORKER_SERVICE"
  print_yes_no worker_enabled systemctl is-enabled --quiet "$WORKER_SERVICE"
  print_yes_no nginx_active systemctl is-active --quiet nginx.service
  print_yes_no postgresql_active systemctl is-active --quiet postgresql.service
  print_yes_no tailscale_active systemctl is-active --quiet tailscaled.service
  print_yes_no default_link_up test "$(cat "/sys/class/net/${iface}/carrier")" = 1
  print_yes_no current_ipv4_private bash -c \
    "ip -4 -o addr show dev '$iface' scope global | awk '{print \\$4}' | grep -Eq '^(10\\.|192\\.168\\.|172\\.(1[6-9]|2[0-9]|3[01])\\.)'"
  print_yes_no current_ipv4_dynamic bash -c \
    "ip -4 -o addr show dev '$iface' scope global | grep -qw dynamic"
  print_yes_no app_loopback_listener bash -c \
    "ss -ltnH '( sport = :8001 )' | grep -Eq '127\\.0\\.0\\.1:8001|\\[::1\\]:8001'"
  print_yes_no postgres_loopback_listener bash -c \
    "ss -ltnH '( sport = :5432 )' | grep -Eq '127\\.0\\.0\\.1:5432|\\[::1\\]:5432'"
  netplan generate
  check_no_running_jobs
  log "Netplan and PostgreSQL preflight passed"
}

depart() {
  local iface
  require_root
  for command_name in awk find install ip mktemp netplan pg_dump pg_restore \
    psql runuser sha256sum sort systemctl; do
    require_command "$command_name"
  done
  [[ ! -e "$ARMED_LINK" && ! -L "$ARMED_LINK" ]] \
    || die "a move is already armed; inspect or run restore-static first"

  iface="$(default_interface)"
  install -d -o root -g root -m 700 "$STATE_ROOT"
  STATE_DIR="$(mktemp -d "${STATE_ROOT}/state.XXXXXXXX")"
  trap cleanup EXIT INT TERM

  check_no_running_jobs
  if service_was_active "$WORKER_SERVICE"; then
    WORKER_WAS_ACTIVE=1
    log "stopping the worker before the final backup"
    systemctl stop "$WORKER_SERVICE"
  fi
  check_no_running_jobs
  create_verified_backup
  stage_dhcp_for_next_boot "$iface"

  log "stopping the web service and flushing filesystems"
  if service_was_active "$APP_SERVICE"; then
    WEB_WAS_ACTIVE=1
    systemctl stop "$APP_SERVICE"
  fi
  sync

  log "powering off; wait until the power LED is off before unplugging"
  DEPART_COMMITTED=1
  if ! systemctl poweroff; then
    DEPART_COMMITTED=0
    die "poweroff request failed"
  fi
}

restore_static() {
  local state_dir
  require_root
  [[ -L "$ARMED_LINK" ]] || die "no armed move configuration was found"
  state_dir="$(readlink -f "$ARMED_LINK")"
  [[ "$state_dir" == "${STATE_ROOT}/"* && -d "$state_dir" ]] \
    || die "armed move state is invalid"
  restore_netplan_from_state "$state_dir"
  rm -f -- "$ARMED_LINK"
  log "original Netplan files restored for the next boot; current network unchanged"
}

postmove() {
  local iface wan_v4 dns_v4
  for command_name in awk curl getent ip ss systemctl; do
    require_command "$command_name"
  done
  iface="$(default_interface)"
  wan_v4="$(curl -4fsS --max-time 10 https://api.ipify.org 2>/dev/null || true)"
  dns_v4="$(getent ahostsv4 gonggibap.com 2>/dev/null | awk 'NR == 1 {print $1}')"

  print_yes_no dhcp_ipv4_received bash -c \
    "ip -4 -o addr show dev '$iface' scope global | grep -qw dynamic"
  print_yes_no tailscale_active systemctl is-active --quiet tailscaled.service
  print_yes_no web_active systemctl is-active --quiet "$APP_SERVICE"
  print_yes_no worker_active systemctl is-active --quiet "$WORKER_SERVICE"
  print_yes_no nginx_active systemctl is-active --quiet nginx.service
  print_yes_no postgresql_active systemctl is-active --quiet postgresql.service
  printf 'dns_a_matches_new_public_ipv4='; \
    [[ -n "$wan_v4" && "$wan_v4" == "$dns_v4" ]] && echo yes || echo no
  printf 'local_home_http_200='; \
    [[ "$(curl -sS -o /dev/null --max-time 10 -w '%{http_code}' \
      http://127.0.0.1:8001/ 2>/dev/null)" == 200 ]] && echo yes || echo no
  printf 'local_map_http_200='; \
    [[ "$(curl -sS -o /dev/null --max-time 10 -w '%{http_code}' \
      http://127.0.0.1:8001/api/map/restaurants 2>/dev/null)" == 200 ]] && echo yes || echo no
  printf 'public_https_200='; \
    [[ "$(curl -sS -o /dev/null --max-time 15 -w '%{http_code}' \
      "${PUBLIC_URL}/" 2>/dev/null)" == 200 ]] && echo yes || echo no
  echo router_port_forwarding_external_check=manual_required
}

router_values() {
  local iface
  for command_name in awk curl ip; do
    require_command "$command_name"
  done
  iface="$(default_interface)"
  printf 'interface=%s\n' "$iface"
  printf 'lan_ipv4='
  ip -4 -o addr show dev "$iface" scope global | awk 'NR == 1 {sub(/\/.*/, "", $4); print $4}'
  printf 'mac_address='
  cat "/sys/class/net/${iface}/address"
  printf 'public_ipv4='
  curl -4fsS --max-time 10 https://api.ipify.org
  echo
}

case "${1:-}" in
  -h|--help|help)
    usage
    ;;
  preflight)
    preflight
    ;;
  depart)
    depart
    ;;
  restore-static)
    restore_static
    ;;
  postmove)
    postmove
    ;;
  router-values)
    router_values
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
