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
    # `cmd && return` keeps set -e from aborting on a failed attempt, and
    # preserves that attempt's exit code. A bare `if` would report 0.
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

# Probe /healthz/ without depending on nginx or a system curl binary.
# Prefer the gunicorn unix socket (what the README documents), then HTTP.
# Fall back to Python urllib so a missing `curl` cannot false-fail a deploy.
probe_health() {
  local path="${1:-/healthz/}"
  local socket="${GUNICORN_SOCKET:-/run/alpha-loans.sock}"
  local http_url="${HEALTHCHECK_URL:-http://127.0.0.1:9000${path}}"
  local code=""

  if [ -S "$socket" ] && command -v curl >/dev/null 2>&1; then
    code=$(curl -sS -o /tmp/alpha-loans-health.body -w "%{http_code}" --max-time 5 \
      --unix-socket "$socket" "http://localhost${path}" 2>/tmp/alpha-loans-health.err || true)
    if [ "$code" = "200" ]; then
      echo "Health check OK (unix://${socket}${path})"
      return 0
    fi
  fi

  if command -v curl >/dev/null 2>&1; then
    code=$(curl -sS -o /tmp/alpha-loans-health.body -w "%{http_code}" --max-time 5 \
      "$http_url" 2>/tmp/alpha-loans-health.err || true)
    if [ "$code" = "200" ]; then
      echo "Health check OK ($http_url)"
      return 0
    fi
  fi

  # Python fallback — always available inside the activated venv.
  if python - "$path" "$socket" "$http_url" <<'PY'
import socket, sys, urllib.error, urllib.request

path, unix_sock, http_url = sys.argv[1], sys.argv[2], sys.argv[3]

class UnixHTTPConnection:
    def __init__(self, sock_path):
        self.sock_path = sock_path
    def request(self):
        payload = (
            f"GET {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
        ).encode()
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(5)
        try:
            s.connect(self.sock_path)
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
        return " 200 " in f" {status} "

ok = False
try:
    if unix_sock:
        try:
            ok = UnixHTTPConnection(unix_sock).request()
        except OSError as exc:
            print(f"socket probe failed: {exc}", file=sys.stderr)
    if not ok:
        with urllib.request.urlopen(http_url, timeout=5) as resp:
            ok = resp.status == 200
except Exception as exc:  # noqa: BLE001
    print(f"http probe failed: {exc}", file=sys.stderr)
    ok = False
sys.exit(0 if ok else 1)
PY
  then
    echo "Health check OK (python probe)"
    return 0
  fi

  echo "Last health HTTP status: ${code:-n/a}" >&2
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
  # activate references unset variables; nounset would abort the deploy.
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

# git pull has already replaced the templates. Publish the matching CSS and
# vendored scripts before migrate, so a database outage cannot leave the new
# pages pointing at the previous stylesheet.
python manage.py collectstatic --noinput
retry "migrate" python manage.py migrate --noinput
sudo systemctl restart Alpha-Loans
# Give workers a moment to bind the socket before the first probe.
sleep 3
sudo systemctl --no-pager --full status Alpha-Loans || true

healthy=0
for i in $(seq 1 18); do
  if probe_health /healthz/; then healthy=1; break; fi
  echo "Waiting for health (attempt $i/18)…"
  # Surface crash-loops early so the next failure log is useful.
  if ! systemctl is-active --quiet Alpha-Loans; then
    echo "Alpha-Loans.service is not active:" >&2
    sudo systemctl --no-pager --full status Alpha-Loans >&2 || true
    sudo journalctl -u Alpha-Loans -n 60 --no-pager >&2 || true
    break
  fi
  sleep 5
done

if [ "$healthy" -ne 1 ]; then
  echo "Health check failed after deploy." >&2
  sudo systemctl --no-pager --full status Alpha-Loans >&2 || true
  sudo journalctl -u Alpha-Loans -n 80 --no-pager >&2 || true
  false
fi

trap - ERR
echo "Deploy OK: $(git rev-parse --short HEAD) (was ${PREV_COMMIT:0:7})"
