# Proofs

## Demo replay config uses banking-replay, build tags, and JWT claim session DLP
- **Level**: Integration
- **Evidence**: `thoughts/scripts/verify-demo-replay-config.sh` PASS; MCP pull showed `http.req.http...` fails at transform time, and `Bearer` must be stripped by extractor regex before reading `jwt.claims.sub`.
- **Status**: PROVEN
- **Date**: 2026-06-18

## Live banking-accounts sessions use JWT sub claims
- **Level**: Live traffic
- **Evidence**: Proxymock MCP snapshot `f2aab847-ca40-4d93-b628-e76a2c20cb2c`, `2026-06-19T01:23:37Z` to `2026-06-19T01:28:37Z`: 458 inbound `banking-accounts` records had plain sessions; 0 were JWT-shaped, redacted, blank, or hex.

## Demo replay config covers daily Speedscale and proxymock runs
- **Level**: Integration
- **Evidence**: `thoughts/scripts/verify-demo-replay-config.sh` PASS; `bash -n` passed for replay scripts; YAML and JSON parsed successfully; `speedctl get dlp-config banking-app-keys` read-back had `hasFilters=false` and ended with `tag_session` in the staging tenant
- **Status**: PROVEN
- **Date**: 2026-06-18
