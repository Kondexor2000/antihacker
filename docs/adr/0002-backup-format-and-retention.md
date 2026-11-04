# ADR 0002: Use authenticated encrypted snapshots with bounded local retention

- Status: accepted
- Date: 2026-11-04

## Context

Database snapshots contain credentials and personal information. They must be
tamper-evident, safely replaceable, and recoverable across encryption-key
rotation.

## Decision

Write versioned AES-256-GCM snapshots with authenticated metadata and a SHA-256
content digest. Write to a temporary file, fsync, then atomically rename. Keep
the newest configured number (10 by default), and retain old key IDs while any
backup still depends on them.

## Alternatives and consequences

- Plain dumps are simpler but expose data and do not authenticate changes.
- CBC with a separate MAC is retained only for legacy recovery; new backups use
  AEAD.
- Local filesystem storage avoids a mandatory cloud dependency but does not
  survive loss of the host or storage system. Production requires independent
  off-host copies, access control, and tested key escrow; none is configured by
  this repository.
- Count-based retention is easy to understand but does not guarantee a
  particular calendar window. Replace it with policy-based/object-lock storage
  when recovery objectives or compliance require it.
