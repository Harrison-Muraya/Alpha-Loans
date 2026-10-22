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
sudo systemctl --no-pager --full status Alpha-Loans

# Post-deploy health gate: /healthz/ must answer 200 (DB reachable).
HEALTH_URL="${HEALTHCHECK_URL:-http://127.0.0.1:9000/healthz/}"
healthy=0
for i in $(seq 1 12); do
  if curl -fsS --max-time 10 "$HEALTH_URL" >/dev/null 2>&1; then healthy=1; echo "Health check OK ($HEALTH_URL)"; break; fi
  echo "Waiting for health (attempt $i/12)…"; sleep 5
done
[ "$healthy" -eq 1 ] || { echo "Health check failed: $HEALTH_URL" >&2; false; }
trap - ERR
echo "Deploy OK: $(git rev-parse --short HEAD) (was ${PREV_COMMIT:0:7})"
