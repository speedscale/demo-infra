# Proofs

## Demo replay config covers daily Speedscale and proxymock runs
- **Level**: Integration
- **Evidence**: `thoughts/scripts/verify-demo-replay-config.sh` PASS; `bash -n` passed for replay scripts; YAML and JSON parsed successfully; `speedctl get dlp-config banking-app-keys` read-back had `hasFilters=false` and ended with `tag_session` in the staging tenant
- **Status**: PROVEN
- **Date**: 2026-06-18
