# ADR 0001: Keep the login-triggered job in the application database

- Status: accepted
- Date: 2026-11-04

## Context

A successful login from a new IP address records login history and requests an
encrypted database snapshot. Losing the request silently is unacceptable, but
backup I/O must not delay or fail interactive authentication.

## Decision

Use SQLite only for local development and PostgreSQL for deployed environments.
Persist `BackupEvent` in the same relational database and transaction as the
corresponding `LoginLog`. A separate polling worker claims jobs with a lease,
bounded retries, and a terminal failed state. Do not run backup creation in the
HTTP request.

## Alternatives and consequences

- A managed broker would provide richer routing and scaling but adds cost,
  credentials, monitoring, and publication failure modes for a low-volume demo.
- SQLite is simple for a local single-process demo but has write-concurrency
  limits and is not the deployment target. PostgreSQL gives the web and worker
  a shared durable queue and supports multiple processes.
- An in-process task or Django signal doing the backup synchronously would
  couple login latency to disk and database dump duration.
- A transactional outbox is appropriate if a future design must commit domain
  changes and publish to an independent broker. The current job row is already
  durable in the same database, so an outbox is redundant.

The database remains a dependency for both authentication and queue durability.
Workers can process the same event at least once after lease expiry; backup
creation is serialized and content-deduplicated. Replace this design when job
volume, queue isolation, or cross-database publication requirements exceed the
measured capacity of the primary database.
