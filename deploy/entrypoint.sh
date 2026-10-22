#!/usr/bin/env sh
# Container entrypoint: publish static, migrate (with retry), then exec CMD.
set -eu
python manage.py collectstatic --noinput
attempt=1; max=5; delay=3
while true; do
  if python manage.py migrate --noinput; then break; fi
  if [ "$attempt" -ge "$max" ]; then echo "migrate failed after $max attempts" >&2; exit 1; fi
  echo "migrate failed (attempt $attempt/$max). Retrying in ${delay}s..." >&2
  sleep "$delay"; attempt=$((attempt+1)); delay=$((delay*2))
done
exec "$@"
