# FakeAccount

FakeAccount is a Django application for account registration and login. Login
from a new IP address queues an encrypted database backup for a separate worker.

## Requirements and local setup

- Python 3.10 or later
- Node.js 18 or later only when rebuilding the React interface

From the repository root in PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(50))"
```

Put the generated value in `DJANGO_SECRET_KEY` in `.env`. Keep it private and
stable; in development it also derives the fallback backup key. Keep
`DJANGO_DEBUG=True` for local development, as in `.env.example`. Then install,
migrate, create an administrator account, and start Django:

```powershell
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver 127.0.0.1:8000
```

Open <http://127.0.0.1:8000/register/> to create an account. SQLite is the
default database. Run `python manage.py test` to execute the test suite.
The sign-in form is at `/login/`; successful sign-in goes to `/account/`.
Sign in to Django admin at <http://127.0.0.1:8000/admin/> with the
superuser you created above. Only staff users can access Django admin and
download backups. Logout from the account page uses POST and is protected by
CSRF.
The username-identity migration stops with a clear error if existing account
names normalize to the same value; rename those accounts before retrying it.

The committed React build is served by Django. To rebuild it after changing
files in `frontend/src/`:

```powershell
cd frontend
npm ci
npm run build
cd ..
```

## Production settings

Before deploying, set these values in the environment or an untracked `.env`:

- `DJANGO_DEBUG=False`
- `DJANGO_SECRET_KEY` to a unique random value of at least 50 characters
- `DJANGO_ALLOWED_HOSTS` to a comma-separated list of exact host names
- `BACKUP_ACTIVE_KEY_ID` and `BACKUP_ENCRYPTION_KEYS` (key format below)
- `DJANGO_TRUSTED_PROXY_IPS` only if a reverse proxy is used
- `DJANGO_CSRF_TRUSTED_ORIGINS` to the exact external HTTPS origins when needed

Startup fails if production secrets or allowed hosts are missing or invalid.
Production uses secure session/CSRF cookies and redirects HTTP to HTTPS by
default. Set `DJANGO_SECURE_SSL_REDIRECT=False` only when HTTPS is terminated
upstream and that proxy's address is included in `DJANGO_TRUSTED_PROXY_IPS`.
Set `DJANGO_HSTS_SECONDS` only after HTTPS is correctly deployed; use
`DJANGO_HSTS_INCLUDE_SUBDOMAINS` or `DJANGO_HSTS_PRELOAD` only when every
affected host is HTTPS-ready. The session cookie is HttpOnly; the CSRF cookie
is readable by same-origin JavaScript for Django admin's CSRF-protected requests.

`X-Forwarded-For` and `X-Forwarded-Proto` are accepted only when the immediate
peer belongs to `DJANGO_TRUSTED_PROXY_IPS` (IP addresses or CIDR ranges). The
proxy must overwrite or append forwarding headers correctly. Without a trusted
proxy, the application uses the socket peer address and ignores these headers.
The development-only Django static-file route is disabled when `DEBUG=False`;
collect static assets with `python manage.py collectstatic` and serve `STATIC_ROOT`
from the production web server.

## PostgreSQL

Set `DJANGO_DATABASE=postgres` and provide `POSTGRES_DB`, `POSTGRES_USER`,
`POSTGRES_PASSWORD`, `POSTGRES_HOST`, and `POSTGRES_PORT`. Then run
`python manage.py migrate` and start the application as above. Keep
`DJANGO_DATABASE=sqlite` to use the default local database.

## Login-triggered backup worker

Run the web application and a worker as separate processes. For example, in a
second PowerShell window:

```powershell
python manage.py process_backups
```

The worker claims persisted queue entries, so a stopped process does not lose
pending work. Restart it after deployment or machine restarts. Failed jobs are
recorded in Django admin and can be retried explicitly:

```powershell
python manage.py retry_backups
```

The worker makes up to five attempts total (the initial attempt plus four
retries) with exponential delays of 30, 60, 120, and 240 seconds. Exhausted jobs
remain failed for inspection and are not retried automatically; `retry_backups`
is an explicit operator action.
The account page shows only the latest job's public status, never its error
details or source IP. The queue is stored in the application database, so no
separate broker or transactional outbox is needed for this single-database
workflow. `python manage.py backup_status` prints a JSON snapshot of database
availability, queue counts, and the oldest pending/processing age for an
external metrics collector or alert rule.

To process only the current pending queue and exit, use
`python manage.py process_backups --once`.

## Backup format, retention, and key recovery

New backups use AES-256-GCM authenticated encryption. Each file contains an
authenticated format/version header and key ID, and is written to a temporary
file before an atomic rename. A cross-process lock serializes backup creation.
`BACKUP_RETENTION_COUNT` controls how many versioned backups are kept (default
10); legacy `backup.enc` files are left untouched.

Generate a key without putting it in source control:

```powershell
python -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

Store it securely and configure `.env` like this:

```text
BACKUP_ACTIVE_KEY_ID=primary
BACKUP_ENCRYPTION_KEYS=primary:<generated-key>
```

Keep independent, access-controlled copies of both the database and all keys
outside the application host. To rotate keys, add the new key to
`BACKUP_ENCRYPTION_KEYS`, change `BACKUP_ACTIVE_KEY_ID`, and create a forced
backup with `python manage.py secure_backup --force`. Retain old keys while any
retained or off-site backup uses them; removing a key makes those backups
unreadable. Before removing an old key, verify recovery by decrypting a backup
with the intended recovery environment. Decrypt the latest backup with
`python manage.py secure_backup --decrypt`, or select a specific version with
`python manage.py secure_backup --decrypt --input <backup-file>`. The command
prints the verified Django fixture JSON to stdout; restore it only into a
matching, intentionally prepared database with `python manage.py loaddata`.
Keep `DJANGO_SECRET_KEY` stable as well as it protects sessions and other
Django signing data.

## Architecture and operations

The design assumptions, threat model, measurable service objectives, flow
diagrams, and ADRs are in [docs/architecture.md](docs/architecture.md).
Operator procedures and the local restore drill are in
[docs/runbooks.md](docs/runbooks.md). These documents explicitly distinguish
the local/cluster demo from a production service: an off-host backup destination,
automated alert delivery, and measured production load/restore results still
depend on deployment infrastructure.

Application logs are JSON written to stdout. Each HTTP response includes an
`X-Request-ID`; valid UUIDs supplied by callers are preserved, and malformed
values are replaced. Logs include that correlation ID, response status and
duration, login outcomes, and backup event IDs without logging passwords or
decrypted data.

Older `AES-256-CBC-HMAC-SHA256` backups remain readable during migration.
`BACKUP_LEGACY_SECRET_KEY` defaults to the current Django secret; if that
secret has already been rotated, set this variable to the old secret to recover
legacy backup files. Verify a new AES-GCM backup before retiring the legacy
secret. Do not send decrypted backup data to an untrusted host.

## HTTPS development server

The built-in HTTPS server is only for local testing, not production:

```powershell
python manage.py generate_cert
python manage.py runhttps
```

Open <https://127.0.0.1:8000/register/>. The browser will warn until the local
CA in `certs/ca.crt` is trusted. For another device on a private network, add
the computer's LAN IP to both `DJANGO_ALLOWED_HOSTS` and
`DJANGO_CSRF_TRUSTED_ORIGINS`, then generate a certificate for that address:

Add the address to the comma-separated values in `.env` (preserve any other
hosts or origins you need). For example:

```text
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,192.168.1.22
DJANGO_CSRF_TRUSTED_ORIGINS=https://127.0.0.1:8000,https://localhost:8000,https://192.168.1.22:8000
```

The trusted origin must include the scheme and port, but no path. Restart the
development server after changing `.env`.

```powershell
python manage.py generate_cert --ip 192.168.1.22
python manage.py runhttps
```

Replace `192.168.1.22` with the computer's actual LAN IP. Do not expose the
development servers to the public internet.

## Docker Compose (local)

The Compose stack runs Django, PostgreSQL, and the persisted backup worker. It
uses HTTP and development settings, so use it for local demonstrations only.
The application image builds the React bundle, collects static files, and runs
as a non-root user. Application and worker logs are written to standard output.

Copy `.env.compose.example` to `.env.compose`, replace the placeholder Django,
database, and backup keys, and start the stack from the repository root:

```powershell
docker compose --env-file .env.compose up --build
```

Open <http://localhost:8000/register/>. The web container applies migrations
before starting Gunicorn. Compose waits for PostgreSQL before starting Django,
then starts the backup worker after the web health check succeeds. To stop the
stack, run `docker compose down`; persistent database and backup volumes remain.
To delete those volumes as well, use `docker compose down --volumes`.

### Test IP-change backups from another device on the LAN

Docker Desktop forwards published-port traffic through its backend, so the
container may see the Docker gateway address instead of each device's address.
For a realistic IP-change demonstration, run the web app and backup worker on
the Windows host while keeping PostgreSQL in Docker. The host process receives
the original client address; the script stops the containerized web/worker,
starts the database, applies migrations, and runs both host processes:

```powershell
.\scripts\run-lan.ps1
```

Open `http://<this-computer-LAN-IP>:8001/register/` from a browser on the same
network. Update `DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS` in
`.env.compose` if the LAN IP or port changes. The script uses Django's local
development server and is for trusted-network demonstrations only. Press
Ctrl+C to stop it; run `docker compose --env-file .env.compose up -d` to return
to the all-container stack.

## Jenkins pipeline

`Jenkinsfile` defines a multibranch pipeline. Configure a Jenkins agent with
Docker, Python, Node.js/npm, and `kubectl`; configure the `container-registry`
username/password credential for GitHub Container Registry; and set
`IMAGE_REPOSITORY` to the image repository you control. The Jenkins agent must
also have a Kubernetes context with permission to update the two deployments in
the `fakeaccount` namespace. The pipeline runs lint and Django checks/tests on
branches, then on `main` builds and publishes an image tagged with the commit
SHA and updates the web and backup-worker deployments. Keep registry and
cluster credentials in Jenkins credentials and agent configuration, never in
the repository. Replace the example image repository in both `Jenkinsfile` and
`deploy/k8s/web.yaml`; if the image is private, configure an image pull Secret
for the cluster as well.

## Kubernetes deployment example

`deploy/k8s/` contains a Kustomize example for the web app, backup worker,
service, TLS ingress, configuration, and shared backup volume. It expects an
external PostgreSQL database, an NGINX ingress controller, a TLS Secret named
`fakeaccount-tls`, and a storage class that supports `ReadWriteMany` volumes.
Replace `fakeaccount.example.com`, `postgres.example.internal`, the image
repository, and the requested storage size for your environment. Create a
`fakeaccount-secrets` Secret in the `fakeaccount` namespace containing
`DJANGO_SECRET_KEY`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, and
`BACKUP_ENCRYPTION_KEYS`; keep it in a secret manager or create it directly in
the cluster, and do not commit its values.

Apply the base resources once, after preparing the namespace secrets and TLS
Secret:

```powershell
kubectl apply -k deploy/k8s
```

The web pod applies migrations before it starts serving requests. The sample is
intentionally configured for one web replica because migrations run during
startup; use a dedicated migration Job before increasing replicas. The cluster
must provide access to the external database, a shared backup volume, and an
off-host backup/key recovery plan. The PVC needs `ReadWriteMany`; many local
Kubernetes installations only provide `ReadWriteOnce` by default, so choose a
suitable storage provisioner or replace this volume design. The example ingress
assumes TLS termination at NGINX and keeps Django's internal SSL redirect off;
the ingress must enforce HTTPS. Before production use, configure
`DJANGO_TRUSTED_PROXY_IPS` to match the ingress controller's actual source
addresses, then validate the cluster's network policy, TLS, secret encryption,
storage durability, and backup restoration process.
