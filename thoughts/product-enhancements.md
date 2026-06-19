# Product Enhancement Notes

## High Priority

- Add a first-class replay auth token provider that bridges DLP and replay auth: extract non-sensitive identity claims before redaction, then generate fresh per-session tokens during replay.
- Replay setup should recommend `jwt_resign` when captured inbound Authorization JWTs are expired or likely to expire before replay. DLP redaction should not be presented as the auth repair path.
- The replay CLI should expose a direct JWT resign option instead of requiring snapshot metadata mutation before `infra replay`.
- DLP recommendations should warn when Authorization redaction would replace a parsable JWT with an opaque placeholder and make JWT resigning impossible.

## Medium Priority

- The DLP UI should generate explicit valid subset filters for each transform chain. Empty filters currently render as invalid filters.
- Replay reports should classify authentication failures separately from application/state failures so expired JWT, malformed JWT, missing auth, and business-rule failures are not conflated.
- Snapshot readiness checks should flag write-heavy scenarios that depend on nondeterministic database seed state before replay starts.

## Low Priority

- Product docs should show the safest order for banking-style traffic: tag session from JWT claims, re-sign inbound replay auth, and redact outbound third-party credentials.
