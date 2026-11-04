# Operations runbooks

Commands below assume the repository root and the configured environment. Do
not paste secrets, decrypted fixtures, or full database dumps into tickets or
chat.

## Login is unavailable or unexpectedly slow

1. Check `/livez/` and `/healthz/` separately. `livez` indicates the process is
   responding; `healthz` also checks the configured database connection.
2. Find the affected response using its `X-Request-ID` and corresponding JSON
   `http.request.complete`, `login.failed`, or `login.succeeded` records.
3. Check database reachability, connection limits, Django/worker logs, and the
   web deployment's restart count before changing password or proxy settings.
4. Confirm the trusted proxy ranges match the actual immediate peer. Do not
   trust all forwarding headers or enable wildcard hosts to work around an
   address mismatch.
5. If only one account is locked, check the configured login-throttling policy;
   do not disable throttling globally. Escalate repeated failures as a security
   event.

## Backup queue is delayed or failed

1. Check that the database is healthy and the worker is running the same
   application release as the web process.
2. Run `python manage.py backup_status` and inspect its database status, job
   counts, and oldest due-job age. Inspect **Admin → Backup events** for
   individual pending, processing, or failed jobs.
   The account page intentionally shows status only; detailed failure text is
   restricted to operators.
3. A processing event older than `BACKUP_PROCESSING_LEASE_SECONDS` can be
   reclaimed. Check for worker restarts, storage-full errors, file permissions,
   lock timeouts, and key configuration before restarting workers repeatedly.
4. Pending retries use a bounded exponential delay. A failed event is the
   dead-letter state after the automatic attempt limit. After addressing the
   cause, run:

   ```powershell
   python manage.py retry_backups
   ```

5. Confirm the event reaches `succeeded`, decrypt the newest snapshot using
   `python manage.py secure_backup --decrypt`, and verify that the expected
   backup exists. A successful job alone is not proof of off-host durability.

## Verify and restore a backup

Perform restore drills in an isolated recovery environment and a new, empty
database. Never point `loaddata` at the live database during a drill.

1. Retrieve the encrypted snapshot and the corresponding key from their
   approved, access-controlled locations. Keep the old key available during
   rotation until every retained copy has been tested.
2. Verify and decrypt to a protected temporary fixture file:

   ```powershell
   python manage.py secure_backup --decrypt --input C:\recovery\backup.enc > C:\recovery\restore-fixture.json
   ```

   The command verifies AES-GCM authentication and the embedded content digest
   before writing fixture data. Restrict access to the temporary file and
   securely remove it after the drill.
3. Configure the recovery environment to use the intended PostgreSQL instance,
   then run migrations and load only into the isolated empty database:

   ```powershell
   python manage.py migrate --noinput
   python manage.py loaddata C:\recovery\restore-fixture.json
   ```

4. Validate login, representative account records, and backup event data. Record
   the snapshot timestamp, commit, start/end time, database size, validation
   outcome, and measured RTO. Do not call the RTO target achieved until this
   procedure has succeeded.
5. Restore the service only after an operator approves the recovered database
   and verifies the active secrets/key set.

The repository does not configure off-host storage, key escrow, or a scheduler.
Set up independent copies and run `python manage.py secure_backup` at least
daily in the deployment scheduler to make the stated 24-hour RPO plausible.
