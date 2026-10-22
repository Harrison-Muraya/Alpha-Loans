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

Tailwind CSS is already built at `theme/static/css/dist/styles.css`. Rebuild it from `theme/static_src` with `npm ci && npm run build` when templates or `src/styles.css` change, and commit the built file. `deploy/deploy.sh` rebuilds it when `npm` is installed and otherwise ships the committed CSS. Chart.js for the dashboard is vendored at `static/loans/chart.umd.min.js`, so the charts do not depend on a CDN. `django-browser-reload` and `pytailwindcss` are dev-only; with `DEBUG=true` and the reload package installed, the dev server exposes `/__reload__/`.

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

## People and roles

User management lives in the app at **User management** (`/manage/users/`), not only in Django admin. It is limited to superusers and members of the **Admin** group. Every change is a POST with a CSRF token, and the action is written to the activity log (the password itself is not).

| Role | Group | What they can do |
| --- | --- | --- |
| Admin | `Admin` | Everything a loan officer can do, plus create and edit users, set passwords, assign roles, deactivate, unlock, and delete |
| Loan officer | `Loan Officer` | Issue and edit loans, record payments, open the archive and activity log |
| Viewer | `Viewer` | Read the same staff pages and export CSV. Cannot change loans, payments, or users |
| Borrower | none | Register, sign in, and see their own loans |

The groups are created on migrate and carry the matching Django permissions (`auth.change_user` for admins, `loans.change_loan` for officers, view permissions for viewers). A staff account that existed before these groups still has write access on the loan book, and still cannot open user management.

Safeguards:

- An admin cannot deactivate, demote, or delete their own account.
- The last superuser cannot be demoted, deactivated, or deleted. Only a superuser can change or delete another superuser.
- Deleting a user who still has live loans is refused unless the admin confirms archival. Those loans are copied to the archive first, then the account is removed.
- A generated temporary password is shown once in the flash message and is not stored in the activity log.
- Unlock clears failed `LoginAttempt` rows for that username.

Everyone who is signed in has **Profile** (`/account/`) to update their name and email and to change their password.

CSV downloads sit next to the existing Excel and PDF exports: loans, payments, the archive, the activity log (respecting the current filters), and, for admins, the user directory. A cell that starts with `=`, `+`, `-`, or `@` is prefixed so a spreadsheet does not treat it as a formula.

The activity log page filters by action, username, and date, and links through to the same CSV.

## Scheduled commands

Both commands live on the `loans` app, so `manage.py` discovers them.

```cron
0 8 * * * /var/www/Alpha-Loans/venv/bin/python /var/www/Alpha-Loans/manage.py mark_overdue_loans
30 2 * * * /var/www/Alpha-Loans/venv/bin/python /var/www/Alpha-Loans/manage.py purge_activity_logs
```

`mark_overdue_loans` sets `status='overdue'` on active, unpaid loans whose due date is before today in Africa/Nairobi. A loan due today is left active. `--dry-run` prints the list and writes nothing.

`purge_activity_logs` deletes `page_visit` rows older than the retention window. Logins, payments, loan changes, and user-management actions are kept. The request middleware also skips a repeated visit to the same path inside `ACTIVITY_LOG_DEDUPE_SECONDS`.

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

`deploy/deploy.sh` is that sequence, plus a Tailwind build when `npm` is installed. It collects static files before migrating, and retries `migrate` up to five times (3s, 6s, 12s, 24s) when the database drops the connection. The service restarts only after migrate succeeds.

## Production with Docker (alternative to bare-metal)

```bash
cp .env.example .env   # set a 50+ char SECRET_KEY, DB_PASSWORD, ALLOWED_HOSTS, USE_HTTPS
docker compose -f docker-compose.prod.yml up -d --build
curl http://127.0.0.1:9000/healthz/   # {"status":"ok","database":"ok"}
```

`docker-compose.prod.yml` runs PostgreSQL 16, Gunicorn (`deploy/gunicorn.conf.py`, non-root, `Dockerfile` healthcheck) and Nginx (`deploy/nginx.conf`, serves `/static/`, proxies `/healthz/`). `deploy/entrypoint.sh` collects static, retries `migrate` with backoff, then execs Gunicorn. Data lives in the `pgdata` volume; back it up with `pg_dump` before upgrades.

## Health check

`GET /healthz/` is unauthenticated, returns `200 {"status":"ok","database":"ok"}` only when the DB answers `SELECT 1`, otherwise `503`. Used by Docker, `deploy/deploy.sh` and the post-deploy smoke test in GitHub Actions.

## GitHub Actions (CI/CD)

```
push / PR ──► CI ──► quality: ruff · pip-audit · Tailwind build (committed CSS must be fresh) · check --deploy
                  ├─► test: PostgreSQL 16 · coverage (artifact uploaded)
                  └─► docker: production image builds (Buildx + GHA cache)
push to main ─► CI green ─► Deploy (environment: production, never cancelled mid-run)
                            SSH → deploy.sh: git pull → pip → Tailwind → collectstatic
                            → migrate (retry) → restart → /healthz/ gate
                            → on any failure: rollback to previous commit + restart
                            → external /healthz/ probe (12 × 10s) fails the run if down
```

`.github/workflows/ci.yml` runs on every push and pull request. `.github/workflows/deploy.yml` starts only after CI succeeds on `main` (or via manual *workflow_dispatch*). If any required secret is missing, the deploy job logs that and **succeeds without deploying**. Weekly Dependabot PRs cover pip, npm, Docker and Actions.

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
