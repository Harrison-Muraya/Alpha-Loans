# Alpha Loans

Django loan book for staff: issue loans, record payments, and keep an archive when a loan is removed. Borrowers can register, sign in, and see their own loans.

The production process is `Alpha-Loans`. The app checkout on the server is `/var/www/Alpha-Loans`.

```
Internet
   ↓
Nginx
   ↓
/run/alpha-loans.sock
   ↓
Gunicorn
   ↓
Django
```

The current site is plain HTTP at `http://197.248.201.233:9000`. HTTPS settings stay off until `USE_HTTPS=true`.

## Local setup

Python 3.12. PostgreSQL matches production. A laptop can use SQLite instead: set `DB_ENGINE=sqlite` in `.env` (and you can omit the `DB_*` variables). Payment recording locks the loan row on PostgreSQL so two people cannot both take the remaining balance. SQLite cannot take that lock; use PostgreSQL if you need to try that case. From the repo root:

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env              # then set SECRET_KEY and the database password
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Tailwind CSS is already built at `theme/static/css/dist/styles.css`. Rebuild it from `theme/static_src` with `npm ci && npm run build` when templates or `src/styles.css` change. `django-browser-reload` and `pytailwindcss` are dev-only; with `DEBUG=true` and the reload package installed, the dev server exposes `/__reload__/`.

```bash
python manage.py test
python manage.py makemigrations --check --dry-run
ruff check .
```

## Environment

Copy `.env.example`. Required:

| Variable | Purpose |
| --- | --- |
| `SECRET_KEY` | Django secret |
| `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | PostgreSQL |
| `DEBUG` | `True` only on a dev machine |

Defaults that keep the current hosts working when the variable is unset:

| Variable | Default |
| --- | --- |
| `ALLOWED_HOSTS` | `197.248.201.233`, `192.168.100.26`, `localhost`, `127.0.0.1` |
| `USE_HTTPS` | `False`. Leave this false for `http://197.248.201.233:9000`. `True` turns on secure cookies, HSTS, and the SSL redirect |
| `TRUSTED_PROXY_IPS` | `127.0.0.1`, `::1`. `X-Forwarded-For` is ignored from any other peer. An empty `REMOTE_ADDR` (gunicorn on a unix socket) is treated as the local proxy |
| `CSRF_TRUSTED_ORIGINS` | the current HTTP and local origins, including port 9000 |
| `LOGIN_FAILURE_LIMIT` | 5 failures for one username |
| `LOGIN_IP_FAILURE_LIMIT` | 50 failures from one IP |
| `LOGIN_LOCKOUT_SECONDS` | 900 |
| `ACTIVITY_LOG_DEDUPE_SECONDS` | 900. `0` logs every GET |
| `ACTIVITY_LOG_RETENTION_DAYS` | 30, for page-visit rows only |

`TIME_ZONE` is `Africa/Nairobi`.

There is no CORS middleware. A previous `CORS_ALLOW_ALL_ORIGINS` setting did nothing and has been removed.

`NPM_BIN_PATH` is optional. Set it when `npm` is not on `PATH` (the old local settings file pointed at `C:\Program Files\nodejs\npm.cmd` on Windows).

## Lending rules

Interest defaults to 20%. The penalty is still a flat rate that staff type in. It is **not** applied when a loan becomes overdue, and a new loan starts at `penalty_rate` 0. That matches what the app actually saved before: migration `0002` changed the database column default to 40, but Django was inserting the model default of 0. The screens still say late payments incur 40%.

Harrison: should `mark_overdue_loans` set `penalty_rate` to 40, or should new loans start at 40%? Until you say so, overdue only changes `status`.

`issue_date` can be set on the add-loan form and defaults to today in Africa/Nairobi, so a loan can be backdated. A blank due date still becomes the end of the current month.

`status` and `is_paid` are one fact stored in two columns (the archive snapshot and the existing screens read `is_paid`). `Loan.save()`, queryset `update()`, and `bulk_update()` force them to match. Migration `0004_sync_paid_status` corrected rows that had already drifted.

Deleting a loan copies the loan and its payments onto the archive, then removes the live rows. The deleted-loans page is the same archive. A borrower who still has a live loan cannot be deleted (`ON DELETE RESTRICT`). After the loans are archived, the user can be deleted and the snapshot keeps the username.

## Scheduled commands

Both commands live on the `loans` app, so `manage.py` discovers them.

```cron
0 8 * * * /var/www/Alpha-Loans/venv/bin/python /var/www/Alpha-Loans/manage.py mark_overdue_loans
30 2 * * * /var/www/Alpha-Loans/venv/bin/python /var/www/Alpha-Loans/manage.py purge_activity_logs
```

`mark_overdue_loans` sets `status='overdue'` on active, unpaid loans whose due date is before today in Africa/Nairobi. A loan due today is left active. `--dry-run` prints the list and writes nothing.

`purge_activity_logs` deletes `page_visit` rows older than the retention window. Logins, payments, and loan changes are kept. The request middleware also skips a repeated visit to the same path inside `ACTIVITY_LOG_DEDUPE_SECONDS`.

## Manual update

GitHub Actions deploys after CI passes on `main` (see below). The same steps by hand:

```bash
cd /var/www/Alpha-Loans
git pull origin main
source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
sudo systemctl daemon-reload
sudo systemctl restart Alpha-Loans
sudo systemctl status Alpha-Loans
sudo journalctl -u Alpha-Loans -n 50
```

`deploy/deploy.sh` is that sequence, plus a Tailwind build when `npm` is installed.

## GitHub Actions

`.github/workflows/ci.yml` runs on every push and pull request: PostgreSQL 16, dependencies, `ruff`, `manage.py check`, `manage.py check --deploy --fail-level WARNING` (with `USE_HTTPS=true` and HSTS preload, which is stricter than the live HTTP site), `makemigrations --check`, and the test suite.

`.github/workflows/deploy.yml` starts only after that CI workflow succeeds on `main`. It SSHes to the server and runs `deploy/deploy.sh`. If any required secret is missing, the deploy job logs that and **succeeds without deploying**.

### Secrets

| Secret | Required | Example |
| --- | --- | --- |
| `SSH_HOST` | yes | `197.248.201.233` |
| `SSH_USER` | yes | the account that owns the checkout |
| `SSH_PRIVATE_KEY` | yes | PEM private key. A single line with `\n` escapes is accepted |
| `DEPLOY_PATH` | yes | `/var/www/Alpha-Loans` |
| `SSH_PORT` | no | `22` when omitted |

### Server setup before the first deploy

1. Install PostgreSQL, Python 3.12, Nginx, and Gunicorn. Install Node only if you want the server to rebuild Tailwind; the compiled CSS is already in git.
2. Clone this repo to `/var/www/Alpha-Loans` and create `venv` there (`python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt`).
3. Create `.env` from `.env.example`. `USE_HTTPS` stays `false` while the site is plain HTTP on port 9000.
4. `python manage.py migrate` and `python manage.py collectstatic --noinput`.
5. Run Gunicorn on the unix socket `/run/alpha-loans.sock` under systemd unit `Alpha-Loans`. Nginx proxies port 9000 to that socket and should set `X-Forwarded-For`.
6. Allow the SSH user to restart the unit without a password prompt. `deploy.sh` runs `systemctl restart Alpha-Loans` and `systemctl status Alpha-Loans`. On Ubuntu the binary is usually `/usr/bin/systemctl` (`which systemctl` on the server). Example sudoers line: `username ALL=(root) NOPASSWD: /usr/bin/systemctl restart Alpha-Loans, /usr/bin/systemctl status Alpha-Loans`.
7. Make sure `git pull` works non-interactively on the server (a deploy key or stored credential for this repo).
8. Install the two cron lines above.
9. Add the GitHub Actions secrets listed above. The private key's public half must be in the server user's `authorized_keys`.

If this database was created before these migrations were committed, `migrate` can fail with "relation already exists". Compare the live tables with `loans/migrations`, then `python manage.py migrate loans <migration> --fake` only for the migrations whose tables are already present. Do not fake a migration that changes `loans_loan.borrower_id` to `ON DELETE RESTRICT` or creates `loans_archivedpayment` / `loans_loginattempt` if those changes are not actually in the database.

Deleting a borrower who still has loans is refused. Delete or archive their loans first. Payments are copied onto the deleted-loan archive before the live rows are removed.

`loan_management/2settings.py` was a local settings file with a hardcoded development secret. It has been removed. If that key was ever used outside a laptop, set a new `SECRET_KEY` in the server `.env` and restart Gunicorn.
