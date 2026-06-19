# Replay tuning harness

Run one workload through replay variants and compare the report metrics:

```bash
quality/scripts/tune-replay.sh dev-decoy banking-notification
```

Default variants are:

- `minimal`: current test config, no hooks.
- `test-transforms`: runs `TUNE_APPLY_TEST_TRANSFORMS_CMD`, then replays.
- `mock-transforms`: runs `TUNE_RESET_TRANSFORMS_CMD` and `TUNE_APPLY_MOCK_TRANSFORMS_CMD`, then replays.

Custom variant file:

```json
{
  "variants": [
    {
      "name": "minimal"
    },
    {
      "name": "mock-only",
      "speedctlArgs": ["--mock-only", "api.stripe.com:443"]
    },
    {
      "name": "test-transform",
      "beforeHook": "TUNE_APPLY_TEST_TRANSFORMS_CMD"
    }
  ]
}
```

Each run writes report JSON, AI summaries, and `results.jsonl` under
`quality/replay-tuning/<cluster>/<workload>/<timestamp>/`.
