# Performance and recovery evidence

## Local HTTP smoke-load baseline

Measured on 2026-11-04 against a local Django development server on Windows
with Python 3.13, `DEBUG=True`, and SQLite. The repository worktree was not a
released build; database size and host hardware were not recorded.

| Workload | Result |
|---|---:|
| Endpoint | `GET /healthz/` (database connection check) |
| Requests / concurrency | 200 / 10 |
| Successful responses | 200 / 200 |
| Elapsed / throughput | 0.584 s / 342.68 requests/s |
| p50 / p95 | 15.70 ms / 37.70 ms |
| p99 / maximum | 272.73 ms / 278.63 ms |

Reproduce with the commands in [architecture.md](architecture.md). This is
only a localhost smoke-load measurement: it excludes authentication,
password-hashing cost, worker throughput, PostgreSQL, TLS, network latency,
and production deployment overhead. The p99 outlier is retained rather than
hidden by averages. It is not evidence that the interactive login SLO is met.

## Restore evidence

`SecureBackupIntegrationTests.test_backup_fixture_restores_into_a_clean_database`
creates an encrypted snapshot, verifies/decrypts it, migrates a separate empty
SQLite database, and loads the fixture there. It validates restored user and
backup-event rows. This proves the fixture path locally, not off-host recovery,
key escrow, a PostgreSQL restore, or the 4-hour RTO target.

Run the test suite with:

```powershell
python manage.py test
```

## Next measurements

Before making capacity claims, repeat with PostgreSQL and a representative
dataset, benchmark the login POST separately, and measure queue age/throughput
while workers restart. Perform the documented off-host restore drill and record
the exact backup size, key source, restore start/end time, and validation
results. Keep baseline and post-change results with the commit and environment
details; optimize only after identifying a bottleneck.
