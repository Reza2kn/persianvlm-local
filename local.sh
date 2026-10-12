#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
command -v docker >/dev/null || { echo 'Install Docker Desktop (macOS/Windows) or Docker Engine + Compose (Linux), then retry.' >&2; exit 1; }
docker compose version >/dev/null
action="${1:-start}"
if [[ "$action" == start ]]; then
  docker info >/dev/null 2>&1 || { echo 'Start Docker and retry.' >&2; exit 1; }
  if [[ ! -f .env ]]; then
    umask 077
    secret() { LC_ALL=C od -An -N24 -tx1 /dev/urandom | tr -d ' \n'; }
    { echo "POSTGRES_PASSWORD=$(secret)"; echo "ARGILLA_AUTH_SECRET_KEY=$(secret)"; echo "OWNER_PASSWORD=$(secret)"; echo "OWNER_API_KEY=$(secret)"; echo 'LOCAL_PORT=6900'; } > .env
  fi
  docker compose up -d --build --wait --wait-timeout 600
  "$0" credentials
  echo 'Open /local to import datasets; open / to annotate.'
  if command -v open >/dev/null; then open "http://localhost:$(sed -n 's/^LOCAL_PORT=//p' .env)/local"; fi
elif [[ "$action" == credentials ]]; then
  test -f .env || { echo 'Run ./local.sh start first.' >&2; exit 1; }
  echo "URL: http://localhost:$(sed -n 's/^LOCAL_PORT=//p' .env)/local"
  echo 'Username: owner'
  sed -n 's/^OWNER_PASSWORD=/Password: /p;s/^OWNER_API_KEY=/API key: /p' .env
elif [[ "$action" == stop ]]; then
  docker compose stop
elif [[ "$action" == status ]]; then
  docker compose ps
elif [[ "$action" == logs ]]; then
  docker compose logs --tail 100 -f
elif [[ "$action" == backup ]]; then
  dest="${2:-backups/$(date +%Y%m%d-%H%M%S)}"
  mkdir -p "$dest"; chmod 700 "$dest"
  docker compose stop local worker argilla
  trap 'docker compose start argilla worker local >/dev/null' EXIT
  docker compose exec -T postgres pg_dump -U postgres -Fc argilla > "$dest/postgres.dump"
  docker compose run --rm --no-deps -T --entrypoint tar local czf - -C /data . > "$dest/local-data.tar.gz"
  cp .env "$dest/credentials.env"; chmod 600 "$dest"/*
  echo "Backup saved: $dest (includes credentials; keep private). See docs/backup.md for restore."
else
  echo 'Usage: ./local.sh {start|stop|status|logs|credentials|backup [directory]}' >&2; exit 2
fi
