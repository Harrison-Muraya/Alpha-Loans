#!/usr/bin/env bash
# Update the running app on the server. Invoked by .github/workflows/deploy.yml
# after CI succeeds on main, or by hand from the deploy checkout.
set -euo pipefail

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
python manage.py migrate --noinput

if command -v npm >/dev/null 2>&1 && [ -f theme/static_src/package-lock.json ]; then
  echo "Building Tailwind CSS"
  (cd theme/static_src && npm ci && npm run build)
else
  echo "npm not installed; keeping the Tailwind CSS already committed in the repo."
fi

python manage.py collectstatic --noinput
sudo systemctl restart Alpha-Loans
sudo systemctl --no-pager --full status Alpha-Loans
