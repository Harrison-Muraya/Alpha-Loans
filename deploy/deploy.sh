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
