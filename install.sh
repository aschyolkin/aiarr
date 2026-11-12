#!/usr/bin/env bash
# aiarr installer: checks Docker, writes .env, prepares directories, builds and starts the stack.
#
#   ./install.sh                                   from a checkout of the repository
#   curl -fsSL https://raw.githubusercontent.com/aschyolkin/aiarr/main/install.sh | bash
#
# Non-interactive use: preset the MAE_* variables (see .env.example) and AIARR_YES=1.
# Existing .env is never overwritten; delete it (or pass AIARR_RECONFIGURE=1, which keeps a
# backup) to configure again. Real downloads stay off (MAE_DRY_RUN=true) unless you opt in.
set -euo pipefail

REPO_URL=${AIARR_REPO:-https://github.com/aschyolkin/aiarr.git}

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

TTY=""
if [ -t 0 ]; then TTY=/dev/stdin; elif ( : </dev/tty ) 2>/dev/null; then TTY=/dev/tty; fi

# ask VAR "Prompt" [default] [secret]: keeps a value preset in the environment.
ask() {
  local var=$1 prompt=$2 default=${3:-} secret=${4:-} value=${!1:-}
  [ -n "$value" ] && return 0
  if [ -n "${AIARR_YES:-}" ] || [ -z "$TTY" ]; then
    printf -v "$var" '%s' "$default"
    return 0
  fi
  if [ -n "$secret" ]; then
    read -rsp "$prompt: " value <"$TTY"
    echo
  else
    read -rp "$prompt${default:+ [$default]}: " value <"$TTY"
  fi
  printf -v "$var" '%s' "${value:-$default}"
}

confirm() { # confirm "Question" -> true only for an explicit yes
  local answer=""
  [ -n "${AIARR_YES:-}" ] || [ -z "$TTY" ] && return 1
  read -rp "$1 [y/N]: " answer <"$TTY"
  [[ $answer =~ ^[Yy] ]]
}

random_key() {
  if command -v openssl >/dev/null 2>&1; then openssl rand -hex 24
  else head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n'; fi
}

# --- prerequisites
command -v docker >/dev/null 2>&1 || die "Docker is required: https://docs.docker.com/engine/install/"
docker compose version >/dev/null 2>&1 || die "the Docker Compose plugin is required (docker compose ...)"
docker info >/dev/null 2>&1 || die "cannot talk to the Docker daemon (is it running, and are you in the docker group?)"

# --- source code
if [ -f "$(dirname "${BASH_SOURCE[0]:-$0}")/compose.yaml" ] 2>/dev/null; then
  cd "$(dirname "${BASH_SOURCE[0]}")"
else
  command -v git >/dev/null 2>&1 || die "git is required to download aiarr"
  target=${AIARR_DIR:-$PWD/aiarr}
  if [ -d "$target/.git" ]; then
    say "Updating $target"
    git -C "$target" pull --ff-only
  else
    say "Downloading aiarr to $target"
    git clone --depth 1 "$REPO_URL" "$target"
  fi
  cd "$target"
fi
ROOT=$PWD
say "Installing in $ROOT"

# --- configuration
if [ -f .env ] && [ -z "${AIARR_RECONFIGURE:-}" ]; then
  say "Keeping the existing .env (set AIARR_RECONFIGURE=1 to configure again)"
else
  [ -f .env ] && cp -p .env ".env.bak.$(date +%Y%m%d-%H%M%S)"
  say "Configuration (press Enter to accept a default)"
  echo "The services below are reached from inside a container: 127.0.0.1 would be the container"
  echo "itself. Use the host address, or host.docker.internal for services on this machine."
  ask MAE_STORE_HOST "Media root holding downloads/ films/ serials/ (one filesystem)" "/srv/media"
  ask MAE_STATE_HOST "Directory for aiarr's database and settings" "$ROOT/state"
  ask MAE_QBIT_URL "qBittorrent Web UI URL" "http://host.docker.internal:8080"
  ask MAE_QBIT_USERNAME "qBittorrent user" "admin"
  ask MAE_QBIT_PASSWORD "qBittorrent password" "" secret
  ask MAE_JELLYFIN_URL "Jellyfin URL" "http://host.docker.internal:8096"
  ask MAE_JELLYFIN_KEY "Jellyfin API key (Dashboard > API Keys)" "" secret
  ask MAE_SEERR_URL "Seerr URL" "http://host.docker.internal:5055"
  ask MAE_SEERR_KEY "Seerr API key (Settings > General)" "" secret
  ask MAE_DEEPSEEK_KEY "DeepSeek API key (optional, Enter to skip)" "" secret
  ask MAE_BIND "Listen address (0.0.0.0 = whole network, 127.0.0.1 = this machine only)" "0.0.0.0"
  ask MAE_PORT "Port" "30280"
  MAE_UID=${MAE_UID:-3000}
  MAE_GID=${MAE_GID:-3000}
  [ -n "${MAE_SEERR_KEY:-}" ] || die "the Seerr API key is required (preset MAE_SEERR_KEY)"
  [ -n "${MAE_JELLYFIN_KEY:-}" ] || die "the Jellyfin API key is required (preset MAE_JELLYFIN_KEY)"
  MAE_API_KEY=${MAE_API_KEY:-$(random_key)}
  MAE_DRY_RUN=${MAE_DRY_RUN:-true}
  if [ "$MAE_DRY_RUN" = true ] && confirm "Enable real downloads now? (otherwise a safe dry-run first)"; then
    MAE_DRY_RUN=false
  fi

  (
    umask 077
    : >.env.new
    for name in MAE_API_KEY MAE_DRY_RUN MAE_STORE_HOST MAE_STATE_HOST MAE_UID MAE_GID MAE_BIND MAE_PORT \
        MAE_QBIT_URL MAE_QBIT_USERNAME MAE_QBIT_PASSWORD MAE_JELLYFIN_URL MAE_JELLYFIN_KEY \
        MAE_SEERR_URL MAE_SEERR_KEY MAE_DEEPSEEK_KEY; do
      value=${!name:-}
      case $value in *\'* | *$'\n'*) rm -f .env.new; die "$name must not contain quotes or newlines" ;; esac
      printf "%s='%s'\n" "$name" "$value" >>.env.new
    done
    printf "MAE_PROVIDERS='[]'\n" >>.env.new
  )
  mv .env.new .env
  chmod 600 .env
  say "Wrote .env (permissions 600). Your API key for machine access: $MAE_API_KEY"
fi

# Values the rest of the script needs, whether .env is new or kept.
set -a
# shellcheck disable=SC1091
. ./.env
set +a
UID_GID="${MAE_UID:-3000}:${MAE_GID:-3000}"

# --- state directory
mkdir -p "$MAE_STATE_HOST" 2>/dev/null || die "cannot create $MAE_STATE_HOST (use sudo mkdir -p, then rerun)"

# --- build
say "Building the image"
docker compose build

# The state directory must belong to the container user. Docker itself can do the chown,
# so no sudo is needed.
image=$(docker compose config --images | head -n1)
docker run --rm --user 0 --entrypoint chown -v "$MAE_STATE_HOST:/state" "$image" "$UID_GID" /state \
  || warn "could not change the owner of $MAE_STATE_HOST to $UID_GID"
chmod 700 "$MAE_STATE_HOST" 2>/dev/null || true

# Missing media directories are created by the container user, so aiarr can write to them.
# Existing ones are left alone.
docker run --rm --user 0 --entrypoint sh -v "$MAE_STORE_HOST:/data/store" "$image" -c '
  for dir in downloads downloads/films downloads/serials films serials; do
    [ -d "/data/store/$dir" ] || { mkdir -p "/data/store/$dir" && chown '"$UID_GID"' "/data/store/$dir"; }
  done' || warn "cannot create the directories in $MAE_STORE_HOST (is it writable?)"

# --- checks from inside a container, which is where the services must be reachable
say "Checking the media directories and the connections"
docker run --rm --user "$UID_GID" --entrypoint sh -v "$MAE_STORE_HOST:/data/store" "$image" -c '
  ok=1
  for dir in downloads/films downloads/serials films serials; do
    [ -w "/data/store/$dir" ] || { echo "not writable for uid $(id -u): /data/store/$dir"; ok=0; }
  done
  [ "$ok" = 1 ]' || warn "fix the permissions of $MAE_STORE_HOST (for example: sudo chown -R $UID_GID $MAE_STORE_HOST/{films,serials}); qBittorrent's user must also be able to write downloads/"
docker compose run --rm --no-deps --entrypoint python worker -c '
import os, urllib.error, urllib.request
for name, var in (("qBittorrent", "MAE_QBIT_URL"), ("Jellyfin", "MAE_JELLYFIN_URL"), ("Seerr", "MAE_SEERR_URL")):
    try:
        urllib.request.urlopen(os.environ[var], timeout=5)
        print("  ok:", name)
    except urllib.error.HTTPError:
        print("  ok:", name)
    except Exception as exc:
        print("  UNREACHABLE:", name, os.environ[var], type(exc).__name__)
' || true

# --- start
say "Starting"
docker compose up -d
printf 'Waiting for the API'
for _ in $(seq 1 60); do
  state=$(docker inspect --format '{{.State.Health.Status}}' "$(docker compose ps -q api)" 2>/dev/null || true)
  [ "$state" = healthy ] && break
  printf '.'
  sleep 2
done
echo
[ "${state:-}" = healthy ] || die "the API did not become healthy; see: docker compose logs api migrate"

host=$(hostname -I 2>/dev/null | awk '{print $1}')
host=${host:-localhost}
say "aiarr is running"
cat <<EOF

  Admin page:  http://$host:$MAE_PORT/admin   (sign in to Seerr as an administrator first)
  Mode:        $([ "$MAE_DRY_RUN" = false ] && echo "real downloads" || echo "DRY-RUN: nothing is downloaded or imported")

Next steps
  1. Add search sources in the admin page ("Sources" / "Источники").
  2. In Seerr add a Radarr and a Sonarr server:
       Radarr  http://<this host>:$MAE_PORT/radarr
       Sonarr  http://<this host>:$MAE_PORT/sonarr
     API key: the MAE_API_KEY from $ROOT/.env, quality profile "1".
  3. Request something, check the chosen release in the admin page, then switch to real
     downloads: set MAE_DRY_RUN=false in .env and run: docker compose up -d

Logs: docker compose logs -f worker      Update: git pull && ./install.sh
EOF
