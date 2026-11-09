#!/usr/bin/env bash
# Update the running app on the server. Invoked by .github/workflows/deploy.yml
# after CI succeeds on main, or by hand from the deploy checkout.
set -euo pipefail

# Postgres has closed the connection while migrate was opening it. With
# set -e that used to skip collectstatic and the service restart, leaving the
# new templates on the previous static files. Retry only this step.
retry() {
  local label=$1
  shift
  local attempt=1
  local max=${MIGRATE_ATTEMPTS:-5}
  local delay=${MIGRATE_DELAY_SECONDS:-3}
  local status
  while true; do
    "$@" && return 0
    status=$?
    if [ "$attempt" -ge "$max" ]; then
      echo "${label} failed on attempt ${attempt} of ${max}." >&2
      return "$status"
    fi
    echo "${label} failed (attempt ${attempt}/${max}). Retrying in ${delay}s." >&2
    sleep "$delay"
    attempt=$((attempt + 1))
    delay=$((delay * 2))
  done
}

# Live gunicorn unit uses /run/alpha-loans/alpha-loans.sock (see systemd status).
# Older docs mentioned /run/alpha-loans.sock — try both, plus any GUNICORN_SOCKET.
resolve_sockets() {
  local candidates=()
  if [ -n "${GUNICORN_SOCKET:-}" ]; then
    candidates+=("$GUNICORN_SOCKET")
  fi
  candidates+=(
    /run/alpha-loans/alpha-loans.sock
    /run/alpha-loans.sock
    /var/run/alpha-loans/alpha-loans.sock
    /var/run/alpha-loans.sock
  )
  local sock
  for sock in "${candidates[@]}"; do
    if [ -S "$sock" ]; then
      printf '%s\n' "$sock"
    fi
  done
}

probe_unix() {
  local path="$1"
  local sock="$2"
  local code=""
  if command -v curl >/dev/null 2>&1; then
    code=$(curl -sS -o /tmp/alpha-loans-health.body -w "%{http_code}" --max-time 5 \
      --unix-socket "$sock" "http://localhost${path}" 2>/tmp/alpha-loans-health.err || true)
    if [ "$code" = "200" ]; then
      echo "Health check OK (unix://${sock}${path})"
      return 0
    fi
  fi
  if python - "$path" "$sock" <<'PY'
import socket, sys
path, sock_path = sys.argv[1], sys.argv[2]
payload = f"GET {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n".encode()
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(5)
try:
    s.connect(sock_path)
    s.sendall(payload)
    data = b""
    while True:
        chunk = s.recv(4096)
        if not chunk:
            break
        data += chunk
finally:
    s.close()
status = data.split(b"\r\n", 1)[0].decode("latin1", "replace")
sys.exit(0 if " 200 " in f" {status} " else 1)
PY
  then
    echo "Health check OK (python unix://${sock}${path})"
    return 0
  fi
  return 1
}

probe_http() {
  local url="$1"
  local code=""
  if command -v curl >/dev/null 2>&1; then
    code=$(curl -sS -o /tmp/alpha-loans-health.body -w "%{http_code}" --max-time 5 \
      "$url" 2>/tmp/alpha-loans-health.err || true)
    if [ "$code" = "200" ]; then
      echo "Health check OK ($url)"
      return 0
    fi
  fi
  if python - "$url" <<'PY'
import sys, urllib.request
url = sys.argv[1]
try:
    with urllib.request.urlopen(url, timeout=5) as resp:
        sys.exit(0 if resp.status == 200 else 1)
except Exception as exc:  # noqa: BLE001
    print(f"http probe failed: {exc}", file=sys.stderr)
    sys.exit(1)
PY
  then
    echo "Health check OK (python $url)"
    return 0
  fi
  return 1
}

# In-process Django probe: proves app + DB without nginx or the unix socket.
# Used when nginx is not bound on 127.0.0.1:9000 (common) and as a last resort.
probe_django() {
  if python <<'PY'
import os, django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "loan_management.settings")
django.setup()
from django.test import RequestFactory
from loans.health import healthz
response = healthz(RequestFactory().get("/healthz/"))
raise SystemExit(0 if response.status_code == 200 else 1)
PY
  then
    echo "Health check OK (in-process Django /healthz/)"
    return 0
  fi
  return 1
}

probe_health() {
  local path="${1:-/healthz/}"
  local sock
  local found=0

  while IFS= read -r sock; do
    found=1
    if probe_unix "$path" "$sock"; then
      return 0
    fi
  done < <(resolve_sockets)

  if [ "$found" -eq 0 ]; then
    echo "No gunicorn unix socket found yet (tried /run/alpha-loans/alpha-loans.sock and /run/alpha-loans.sock)." >&2
  fi

  # Nginx may listen only on the public interface, not 127.0.0.1.
  local urls=(
    "${HEALTHCHECK_URL:-}"
    "http://127.0.0.1:9000${path}"
    "http://127.0.0.1${path}"
  )
  local url
  for url in "${urls[@]}"; do
    [ -n "$url" ] || continue
    if probe_http "$url"; then
      return 0
    fi
  done

  if systemctl is-active --quiet Alpha-Loans 2>/dev/null && probe_django; then
    return 0
  fi

  echo "Last health curl status: see /tmp/alpha-loans-health.err" >&2
  if [ -f /tmp/alpha-loans-health.err ]; then
    sed 's/^/curl: /' /tmp/alpha-loans-health.err >&2 || true
  fi
  if [ -f /tmp/alpha-loans-health.body ]; then
    echo "Body (first 200 bytes):" >&2
    head -c 200 /tmp/alpha-loans-health.body >&2 || true
    echo >&2
  fi
  return 1
}

DEPLOY_PATH="${DEPLOY_PATH:-/var/www/Alpha-Loans}"
cd "$DEPLOY_PATH"

if [ -f venv/bin/activate ]; then
  set +u
  # shellcheck disable=SC1091
  source venv/bin/activate
  set -u
elif [ -f .venv/bin/activate ]; then
  set +u
  # shellcheck disable=SC1091
  source .venv/bin/activate
  set -u
else
  echo "No virtualenv at $DEPLOY_PATH/venv" >&2
  exit 1
fi

PREV_COMMIT="$(git rev-parse HEAD)"
rollback() {
  echo "Deploy failed — rolling back to ${PREV_COMMIT}" >&2
  git reset --hard "$PREV_COMMIT" || true
  pip install -r requirements.txt || true
  python manage.py collectstatic --noinput || true
  python manage.py migrate --noinput || true
  sudo systemctl restart Alpha-Loans || true
}
trap 'rollback' ERR

git pull --ff-only origin main
pip install -r requirements.txt

if command -v npm >/dev/null 2>&1 && [ -f theme/static_src/package-lock.json ]; then
  echo "Building Tailwind CSS"
  (cd theme/static_src && npm ci && npm run build)
else
  echo "npm not installed; keeping the Tailwind CSS already committed in the repo."
fi

python manage.py collectstatic --noinput
retry "migrate" python manage.py migrate --noinput
sudo systemctl restart Alpha-Loans
sleep 3
sudo systemctl --no-pager --full status Alpha-Loans || true

healthy=0
for i in $(seq 1 18); do
  if probe_health /healthz/; then healthy=1; break; fi
  echo "Waiting for health (attempt $i/18)…"
  if ! systemctl is-active --quiet Alpha-Loans; then
    echo "Alpha-Loans.service is not active:" >&2
    sudo systemctl --no-pager --full status Alpha-Loans >&2 || true
    break
  fi
  sleep 5
done

if [ "$healthy" -ne 1 ]; then
  echo "Health check failed after deploy." >&2
  sudo systemctl --no-pager --full status Alpha-Loans >&2 || true
  # journalctl often needs a password for this deploy user — never fail on it.
  sudo -n journalctl -u Alpha-Loans -n 80 --no-pager >&2 || true
  false
fi

trap - ERR
echo "Deploy OK: $(git rev-parse --short HEAD) (was ${PREV_COMMIT:0:7})"
