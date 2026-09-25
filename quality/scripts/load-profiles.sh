#!/usr/bin/env bash
# Source before connecting so unsupported service/profile pairs fail locally.
configure_load_profile() {
  local profile=$1 service=$2
  load_args=()
  case "$profile:$service" in
    regression:*) return 0 ;;
    gateway-ramp:banking-gateway)
      load_args=(--stage vus=1,for=30s --stage vus=5,for=60s,ramp=30s
        --stage vus=10,for=90s,ramp=30s --stage vus=2,for=60s)
      load_p95_ms=1500
      ;;
    fraud-spike:banking-fraud)
      load_args=(--stage vus=2,for=30s --stage vus=20,for=30s,ramp=5s
        --stage vus=2,for=60s)
      load_p95_ms=500
      ;;
    ai-soak:banking-ai)
      load_args=(--vus 2 --for 5m)
      load_p95_ms=2000
      ;;
    *) echo "Unsupported load profile/service: $profile/$service" >&2; return 1 ;;
  esac
  load_args+=(--timeout 8m --fail-if "latency.p95 > $load_p95_ms"
    --fail-if 'requests.total < 1')
}
