# Proofs

## Demo replay config uses banking-replay, build tags, and valid tag_session DLP
- **Level**: Integration
- **Evidence**: `thoughts/scripts/verify-demo-replay-config.sh` PASS; `speedctl get dlp-config banking-app-keys` read-back had `hasFilters=false` and ended with `tag_session` in the staging tenant
- **Status**: PROVEN
- **Date**: 2026-06-18
