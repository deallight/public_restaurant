#!/usr/bin/env bash

set -Eeuo pipefail

umask 027

readonly APP_ROOT="/srv/app"
readonly RELEASES_DIR="${APP_ROOT}/releases"
readonly SHARED_DIR="${APP_ROOT}/shared"
readonly CURRENT_LINK="${APP_ROOT}/current"
readonly CANONICAL_SCRIPT="${APP_ROOT}/bin/deploy-public-restaurant"
readonly REPOSITORY="git@github.com:deallight/public_restaurant.git"
readonly BRANCH="main"
readonly APP_USER="deploy"
readonly APP_GROUP="deploy"
readonly SERVICE_NAME="public-restaurant.service"
readonly WORKER_SERVICE_NAME="public-restaurant-worker.service"
readonly WORKER_UNIT_SOURCE="deploy/public-restaurant-worker.service"
readonly WORKER_UNIT_PATH="/etc/systemd/system/${WORKER_SERVICE_NAME}"
readonly PROD_ENV_FILE="${SHARED_DIR}/public_restaurant.env"
readonly TEST_ENV_FILE="${SHARED_DIR}/public_restaurant_test.env"
readonly SHARED_VAR_DIR="${SHARED_DIR}/var"
readonly BACKUP_DIR="/srv/backups/public-restaurant"
readonly DATABASE_NAME="public_restaurant"
readonly APP_HOST="127.0.0.1"
readonly APP_PORT="8001"
readonly PREFLIGHT_PORT="18001"
readonly PUBLIC_URL="https://gonggibap.com"
readonly LOCK_FILE="/run/lock/public-restaurant-deploy.lock"

SKIP_TESTS=0
SWITCHED=0
DEPLOY_SUCCEEDED=0
PREFLIGHT_STARTED=0
PREVIOUS_RELEASE=""
NEW_RELEASE=""
PREFLIGHT_UNIT=""
PUBLIC_ASSET_COPY=""
APP_HOME=""

usage() {
  cat <<'EOF'
Usage: sudo /srv/app/bin/deploy-public-restaurant [--skip-tests]

Deploy the latest GitHub main commit to the N150 production service.

The default workflow creates a new release, installs dependencies, runs the
isolated PostgreSQL integration suite, creates a PostgreSQL backup, applies and
verifies reviewed migrations, starts a production preflight server on
127.0.0.1:18001, atomically switches /srv/app/current, restarts the web and
background worker services, and verifies local and public HTTP responses. A
failed post-switch check automatically restores the previous release.

Options:
  --skip-tests  Skip the isolated PostgreSQL test suite. Use only when the
                test environment is unavailable and the risk is accepted.
  -h, --help    Show this help text.
EOF
}

log() {
  printf '[deploy] %s\n' "$*"
}

warn() {
  printf '[deploy] WARNING: %s\n' "$*" >&2
}

die() {
  printf '[deploy] ERROR: %s\n' "$*" >&2
  exit 1
}

run_as_app() {
  runuser -u "$APP_USER" -- env HOME="$APP_HOME" "$@"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

assert_private_environment_file() {
  local path="$1"
  local mode owner

  [[ -f "$path" ]] || die "environment file not found: $path"
  mode="$(stat -c '%a' "$path")"
  owner="$(stat -c '%U' "$path")"
  [[ "$owner" == "root" || "$owner" == "$APP_USER" ]] \
    || die "environment file must be owned by root or ${APP_USER}: $path"
  (( 10#$mode % 100 == 0 )) \
    || die "environment file must not be readable by group or others: $path"
}

assert_expected_worktree_state() {
  local path="$1"
  local line
  local status_output
  local unexpected=0

  if ! status_output="$(
    run_as_app git -C "$path" status --porcelain --untracked-files=all
  )"; then
    die "could not inspect Git worktree: $path"
  fi
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    [[ "$line" == "?? var" ]] && continue
    printf '[deploy] unexpected worktree entry: %s\n' "$line" >&2
    unexpected=1
  done <<< "$status_output"

  (( unexpected == 0 )) || die "worktree is not clean: $path"
}

wait_for_http() {
  local expected_status="$1"
  local url="$2"
  local label="$3"
  local attempts="${4:-30}"
  local status=""
  local attempt

  for ((attempt = 1; attempt <= attempts; attempt += 1)); do
    if status="$(curl --silent --show-error --output /dev/null \
      --max-time 10 --write-out '%{http_code}' "$url" 2>/dev/null)"; then
      if [[ "$status" == "$expected_status" ]]; then
        log "${label}: HTTP ${status}"
        return 0
      fi
    fi
    sleep 1
  done

  printf '[deploy] %s expected HTTP %s but received %s\n' \
    "$label" "$expected_status" "${status:-connection failure}" >&2
  return 1
}

stop_preflight() {
  if (( PREFLIGHT_STARTED == 1 )) && [[ -n "$PREFLIGHT_UNIT" ]]; then
    systemctl stop "$PREFLIGHT_UNIT" >/dev/null 2>&1 || true
    PREFLIGHT_STARTED=0
  fi
}

atomic_switch() {
  local target="$1"
  local suffix="$2"
  local pending_link="${APP_ROOT}/.current-${suffix}-$$"

  [[ ! -e "$pending_link" && ! -L "$pending_link" ]] \
    || die "temporary deployment link already exists: $pending_link"
  run_as_app ln -s "$target" "$pending_link"
  run_as_app mv -Tf "$pending_link" "$CURRENT_LINK"
}

rollback_release() {
  [[ -n "$PREVIOUS_RELEASE" ]] || return 1
  warn "restoring previous release: $PREVIOUS_RELEASE"
  systemctl stop "$WORKER_SERVICE_NAME" >/dev/null 2>&1 || true
  atomic_switch "$PREVIOUS_RELEASE" "rollback"
  systemctl restart "$SERVICE_NAME"
  if [[ -f "$PREVIOUS_RELEASE/app/worker.py" ]]; then
    systemctl restart "$WORKER_SERVICE_NAME"
  fi
  if wait_for_http 200 "http://${APP_HOST}:${APP_PORT}/" "rollback upstream" 30; then
    warn "rollback completed"
    return 0
  fi
  warn "automatic rollback did not restore HTTP service; inspect systemd immediately"
  return 1
}

cleanup() {
  local exit_code="$?"
  trap - EXIT INT TERM
  set +e
  stop_preflight
  [[ -z "$PUBLIC_ASSET_COPY" ]] || rm -f "$PUBLIC_ASSET_COPY"

  if (( exit_code != 0 && SWITCHED == 1 && DEPLOY_SUCCEEDED == 0 )); then
    rollback_release || true
  fi

  exit "$exit_code"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

while (( $# > 0 )); do
  case "$1" in
    --skip-tests)
      SKIP_TESTS=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      die "unknown option: $1"
      ;;
  esac
  shift
done

(( EUID == 0 )) || die "run this script with sudo"

for command_name in \
  cmp curl flock getent git grep install lsof mktemp mv pg_dump pg_restore \
  python3 readlink runuser sort stat systemctl systemd-run; do
  require_command "$command_name"
done

APP_HOME="$(getent passwd "$APP_USER" | cut -d: -f6)"
[[ -n "$APP_HOME" && -d "$APP_HOME" ]] || die "home directory not found for ${APP_USER}"

exec 9>"$LOCK_FILE"
flock -n 9 || die "another deployment is already running"

[[ -L "$CURRENT_LINK" ]] || die "current release link not found: $CURRENT_LINK"
[[ -d "$RELEASES_DIR" ]] || die "release directory not found: $RELEASES_DIR"
[[ -d "$SHARED_VAR_DIR" ]] || die "shared var directory not found: $SHARED_VAR_DIR"
assert_private_environment_file "$PROD_ENV_FILE"
if (( SKIP_TESTS == 0 )); then
  assert_private_environment_file "$TEST_ENV_FILE"
fi

PREVIOUS_RELEASE="$(readlink -f "$CURRENT_LINK")"
[[ "$PREVIOUS_RELEASE" == "${RELEASES_DIR}/"* ]] \
  || die "current release is outside the release directory: $PREVIOUS_RELEASE"
assert_expected_worktree_state "$CURRENT_LINK"

if ! systemctl is-active --quiet "$SERVICE_NAME"; then
  die "${SERVICE_NAME} is not active; diagnose the current service before deploying"
fi
systemctl is-active --quiet postgresql.service \
  || die "postgresql.service is not active"
systemctl is-active --quiet nginx.service \
  || die "nginx.service is not active"

CURRENT_MAIN_PID="$(systemctl show "$SERVICE_NAME" --property=MainPID --value)"
[[ "$CURRENT_MAIN_PID" =~ ^[1-9][0-9]*$ ]] || die "invalid systemd MainPID"
mapfile -t APP_LISTENER_PIDS < <(
  lsof -t -iTCP:"$APP_PORT" -sTCP:LISTEN 2>/dev/null | sort -u
)
(( ${#APP_LISTENER_PIDS[@]} == 1 )) \
  || die "expected exactly one listener on ${APP_HOST}:${APP_PORT}"
[[ "${APP_LISTENER_PIDS[0]}" == "$CURRENT_MAIN_PID" ]] \
  || die "port ${APP_PORT} is owned by a process outside ${SERVICE_NAME}"
LISTENER_DETAIL="$(
  lsof -nP -a -p "$CURRENT_MAIN_PID" -iTCP:"$APP_PORT" -sTCP:LISTEN
)"
grep -Fq "${APP_HOST}:${APP_PORT} (LISTEN)" <<< "$LISTENER_DETAIL" \
  || die "${SERVICE_NAME} must listen only on ${APP_HOST}:${APP_PORT}"
wait_for_http 200 "http://${APP_HOST}:${APP_PORT}/" "current upstream" 5
wait_for_http 200 "${PUBLIC_URL}/" "current public service" 5

TARGET_LINE="$(run_as_app git ls-remote "$REPOSITORY" "refs/heads/${BRANCH}")"
TARGET_SHA="${TARGET_LINE%%[[:space:]]*}"
[[ "$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]] || die "could not resolve GitHub ${BRANCH}"
SHORT_SHA="${TARGET_SHA:0:7}"
CURRENT_SHA="$(run_as_app git -C "$CURRENT_LINK" rev-parse HEAD)"
[[ "$CURRENT_SHA" =~ ^[0-9a-f]{40}$ ]] || die "current release commit is invalid"

log "current commit: ${CURRENT_SHA:0:7}"
log "target commit:  ${SHORT_SHA}"
if [[ "$CURRENT_SHA" == "$TARGET_SHA" ]]; then
  log "production already runs the latest ${BRANCH}; nothing to deploy"
  DEPLOY_SUCCEEDED=1
  exit 0
fi

NEW_RELEASE="${RELEASES_DIR}/main-${SHORT_SHA}"
if [[ -e "$NEW_RELEASE" ]]; then
  [[ -d "$NEW_RELEASE/.git" ]] || die "existing release is not a Git checkout: $NEW_RELEASE"
  EXISTING_SHA="$(run_as_app git -C "$NEW_RELEASE" rev-parse HEAD)"
  [[ "$EXISTING_SHA" == "$TARGET_SHA" ]] \
    || die "existing release has an unexpected commit: $NEW_RELEASE"
  assert_expected_worktree_state "$NEW_RELEASE"
  log "resuming existing release: $NEW_RELEASE"
else
  log "cloning ${BRANCH} into $NEW_RELEASE"
  run_as_app git clone --branch "$BRANCH" --single-branch "$REPOSITORY" "$NEW_RELEASE"
  run_as_app git -C "$NEW_RELEASE" checkout --detach "$TARGET_SHA"
fi

[[ "$(run_as_app git -C "$NEW_RELEASE" rev-parse HEAD)" == "$TARGET_SHA" ]] \
  || die "release commit verification failed"

log "creating the release virtual environment"
run_as_app python3 -m venv "$NEW_RELEASE/.venv"
run_as_app "$NEW_RELEASE/.venv/bin/pip" install \
  --disable-pip-version-check --requirement "$NEW_RELEASE/requirements.txt"

if [[ -L "$NEW_RELEASE/var" ]]; then
  [[ "$(readlink -f "$NEW_RELEASE/var")" == "$SHARED_VAR_DIR" ]] \
    || die "release var link points to an unexpected target"
elif [[ -e "$NEW_RELEASE/var" ]]; then
  die "release var path exists and is not the shared symlink"
else
  run_as_app ln -s "$SHARED_VAR_DIR" "$NEW_RELEASE/var"
fi

run_as_app sh -c \
  'grep -qxF "/var" "$1" || printf "/var\n" >> "$1"' \
  sh "$NEW_RELEASE/.git/info/exclude"
assert_expected_worktree_state "$NEW_RELEASE"

if (( SKIP_TESTS == 0 )); then
  log "running the isolated PostgreSQL integration suite"
  TEST_UNIT="public-restaurant-tests-${SHORT_SHA}-$$"
  systemd-run --quiet --wait --pipe --collect \
    --unit="$TEST_UNIT" \
    --uid="$APP_USER" \
    --gid="$APP_GROUP" \
    --setenv="HOME=${APP_HOME}" \
    --working-directory="$NEW_RELEASE" \
    --property="EnvironmentFile=${TEST_ENV_FILE}" \
    /bin/sh -c \
      'export TEST_DATABASE_URL="$DATABASE_URL"; exec "$1" -m unittest discover -s tests' \
      sh "$NEW_RELEASE/.venv/bin/python"
else
  warn "isolated PostgreSQL tests were explicitly skipped"
fi

log "creating a production PostgreSQL backup"
install -d -o postgres -g postgres -m 700 "$BACKUP_DIR"
BACKUP_FILE="${BACKUP_DIR}/predeploy-${SHORT_SHA}-$(date +%Y%m%d-%H%M%S).dump"
runuser -u postgres -- sh -c \
  'umask 077; exec pg_dump --format=custom --dbname="$1" --file="$2"' \
  sh "$DATABASE_NAME" "$BACKUP_FILE"
runuser -u postgres -- pg_restore --list "$BACKUP_FILE" >/dev/null
[[ -s "$BACKUP_FILE" ]] || die "PostgreSQL backup is empty"
log "backup verified: $BACKUP_FILE"

log "applying reviewed production PostgreSQL migrations"
MIGRATION_UNIT="public-restaurant-migrate-${SHORT_SHA}-$$"
systemd-run --quiet --wait --pipe --collect \
  --unit="$MIGRATION_UNIT" \
  --uid="$APP_USER" \
  --gid="$APP_GROUP" \
  --setenv="HOME=${APP_HOME}" \
  --working-directory="$NEW_RELEASE" \
  --property="EnvironmentFile=${PROD_ENV_FILE}" \
  "$NEW_RELEASE/.venv/bin/python" -m scripts.init_db --apply

log "checking production PostgreSQL schema compatibility"
SCHEMA_UNIT="public-restaurant-schema-${SHORT_SHA}-$$"
systemd-run --quiet --wait --pipe --collect \
  --unit="$SCHEMA_UNIT" \
  --uid="$APP_USER" \
  --gid="$APP_GROUP" \
  --setenv="HOME=${APP_HOME}" \
  --working-directory="$NEW_RELEASE" \
  --property="EnvironmentFile=${PROD_ENV_FILE}" \
  "$NEW_RELEASE/.venv/bin/python" -m scripts.check_db_schema

PREFLIGHT_UNIT="public-restaurant-preflight-${SHORT_SHA}-$$"
systemctl stop "$PREFLIGHT_UNIT" >/dev/null 2>&1 || true
if lsof -t -iTCP:"$PREFLIGHT_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  die "preflight port ${PREFLIGHT_PORT} is already in use"
fi

log "starting the production preflight server on ${APP_HOST}:${PREFLIGHT_PORT}"
systemd-run --quiet --collect \
  --unit="$PREFLIGHT_UNIT" \
  --uid="$APP_USER" \
  --gid="$APP_GROUP" \
  --setenv="HOME=${APP_HOME}" \
  --working-directory="$NEW_RELEASE" \
  --property="EnvironmentFile=${PROD_ENV_FILE}" \
  "$NEW_RELEASE/.venv/bin/python" -u -m app.server \
    --host "$APP_HOST" --port "$PREFLIGHT_PORT"
PREFLIGHT_STARTED=1

wait_for_http 200 "http://${APP_HOST}:${PREFLIGHT_PORT}/" "preflight home"
wait_for_http 200 "http://${APP_HOST}:${PREFLIGHT_PORT}/api/map/restaurants" "preflight map"
wait_for_http 200 "http://${APP_HOST}:${PREFLIGHT_PORT}/static/app.js" "preflight static"
wait_for_http 401 "http://${APP_HOST}:${PREFLIGHT_PORT}/admin" "preflight admin guard"
stop_preflight

[[ -f "$NEW_RELEASE/$WORKER_UNIT_SOURCE" ]] \
  || die "worker systemd unit is missing from the release"
install -o root -g root -m 644 \
  "$NEW_RELEASE/$WORKER_UNIT_SOURCE" "${WORKER_UNIT_PATH}.next"
mv -Tf "${WORKER_UNIT_PATH}.next" "$WORKER_UNIT_PATH"
systemctl daemon-reload

[[ "$(readlink -f "$CURRENT_LINK")" == "$PREVIOUS_RELEASE" ]] \
  || die "current release changed while deployment was running"

log "switching current to $NEW_RELEASE"
atomic_switch "$NEW_RELEASE" "$SHORT_SHA"
SWITCHED=1

systemctl restart "$SERVICE_NAME"
systemctl enable "$WORKER_SERVICE_NAME" >/dev/null
systemctl restart "$WORKER_SERVICE_NAME"
wait_for_http 200 "http://${APP_HOST}:${APP_PORT}/" "production upstream"
wait_for_http 200 "http://${APP_HOST}:${APP_PORT}/api/map/restaurants" "production map"
wait_for_http 401 "http://${APP_HOST}:${APP_PORT}/admin" "production admin guard"

NEW_MAIN_PID="$(systemctl show "$SERVICE_NAME" --property=MainPID --value)"
[[ "$NEW_MAIN_PID" =~ ^[1-9][0-9]*$ ]] || die "new systemd MainPID is invalid"
[[ "$(readlink -f "/proc/${NEW_MAIN_PID}/cwd")" == "$NEW_RELEASE" ]] \
  || die "systemd process is not running from the new release"
systemctl is-active --quiet "$WORKER_SERVICE_NAME" \
  || die "${WORKER_SERVICE_NAME} is not active after deployment"
WORKER_MAIN_PID="$(systemctl show "$WORKER_SERVICE_NAME" --property=MainPID --value)"
[[ "$WORKER_MAIN_PID" =~ ^[1-9][0-9]*$ ]] || die "worker systemd MainPID is invalid"
[[ "$(readlink -f "/proc/${WORKER_MAIN_PID}/cwd")" == "$NEW_RELEASE" ]] \
  || die "worker process is not running from the new release"

wait_for_http 200 "${PUBLIC_URL}/" "public home"
wait_for_http 200 "${PUBLIC_URL}/api/map/restaurants" "public map"
wait_for_http 200 "${PUBLIC_URL}/static/app.js" "public static"
wait_for_http 401 "${PUBLIC_URL}/admin" "public admin guard"

PUBLIC_ASSET_COPY="$(mktemp /tmp/public-restaurant-app-js.XXXXXX)"
curl --fail --silent --show-error --max-time 20 \
  "${PUBLIC_URL}/static/app.js" --output "$PUBLIC_ASSET_COPY"
cmp --silent "$NEW_RELEASE/app/static/app.js" "$PUBLIC_ASSET_COPY" \
  || die "public static asset does not match the new release"

install -d -o root -g root -m 755 "$(dirname "$CANONICAL_SCRIPT")"
install -o root -g root -m 755 \
  "$NEW_RELEASE/scripts/deploy_n150.sh" "${CANONICAL_SCRIPT}.next"
mv -Tf "${CANONICAL_SCRIPT}.next" "$CANONICAL_SCRIPT"

DEPLOY_SUCCEEDED=1
log "deployment completed successfully"
log "release: $NEW_RELEASE"
log "previous release preserved: $PREVIOUS_RELEASE"
log "backup: $BACKUP_FILE"
