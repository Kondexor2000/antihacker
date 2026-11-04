# ADR 0003: Trust forwarded headers only from configured proxy peers

- Status: accepted
- Date: 2026-11-04

## Context

The application uses client addresses for login history and needs the correct
HTTPS scheme behind a reverse proxy. Forwarding headers are attacker-controlled
when received directly from the internet.

## Decision

Ignore `X-Forwarded-For` and `X-Forwarded-Proto` unless the immediate socket peer
is in `DJANGO_TRUSTED_PROXY_IPS`. Parse proxy chains from the trusted end and
use the rightmost untrusted address. Operators must configure exact proxy
addresses/CIDRs and ensure the proxy overwrites or appends headers correctly.

## Alternatives and consequences

- Trusting every forwarded header is easy to deploy but permits IP and HTTPS
  spoofing.
- Ignoring all forwarding headers is safe for direct deployments but breaks
  client address and HTTPS detection behind a proxy.
- Exact peer configuration is operationally stricter; update it when ingress
  topology changes. Revisit this decision if the hosting platform provides a
  different authenticated client-address mechanism.
