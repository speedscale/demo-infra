#!/usr/bin/env bash
set -euo pipefail

# Verifies Speedscale TLS-out trust before replays run.
#
# Two invariants, both violated in the past without any test failing:
#   1. The control-plane secret speedscale/speedscale-certs must exist. The chart
#      creates it as a PreSync hook; Argo hook cleanup has deleted it, which leaves
#      the operator unable to provision TLS-out and crash-looping on restart.
#   2. In every app namespace copy, ca-certificates.crt (what the SUT trusts via
#      SSL_CERT_FILE) must contain the CA in tls.crt (what the responder signs
#      mock certs with). The operator writes these copies once and never
#      reconciles them, so a control-plane CA rotation strands them; a mismatch
#      makes every mock TLS handshake fail while status-code assertions stay green.
#
# Usage: check-speedscale-certs.sh <namespace> [<namespace>...]
# Assumes kubectl already points at the target cluster (connect-cluster.sh).

if [ $# -lt 1 ]; then
  echo "Usage: $0 <namespace> [<namespace>...]"
  exit 1
fi

fail=0

if ! kubectl get secret speedscale-certs -n speedscale >/dev/null 2>&1; then
  echo "FAIL: secret speedscale/speedscale-certs is missing." >&2
  echo "      The operator cannot provision TLS-out and will crash-loop on restart." >&2
  echo "      Fix: sync the speedscale-operator ArgoCD app (PreSync hooks recreate it), then restart the operator." >&2
  fail=1
fi

workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT

for ns in "$@"; do
  if ! kubectl get secret speedscale-certs -n "$ns" >/dev/null 2>&1; then
    # No copy yet: the operator creates a fresh, consistent one at next replay.
    echo "OK:   $ns has no speedscale-certs copy (operator will create one)"
    continue
  fi

  kubectl get secret speedscale-certs -n "$ns" -o jsonpath='{.data.ca-certificates\.crt}' | base64 -d > "$workdir/$ns-bundle.crt" || true
  kubectl get secret speedscale-certs -n "$ns" -o jsonpath='{.data.tls\.crt}' | base64 -d > "$workdir/$ns-tls.crt" || true

  if [ ! -s "$workdir/$ns-tls.crt" ]; then
    echo "FAIL: $ns/speedscale-certs has no tls.crt" >&2
    fail=1
    continue
  fi

  if [ ! -s "$workdir/$ns-bundle.crt" ]; then
    echo "FAIL: $ns/speedscale-certs has no ca-certificates.crt bundle" >&2
    fail=1
    continue
  fi

  if openssl verify -CAfile "$workdir/$ns-bundle.crt" "$workdir/$ns-tls.crt" >/dev/null 2>&1; then
    echo "OK:   $ns/speedscale-certs bundle trusts its signing cert"
  else
    echo "FAIL: $ns/speedscale-certs ca-certificates.crt does NOT contain the CA in tls.crt." >&2
    echo "      Mock TLS handshakes in $ns will fail with CERTIFICATE_VERIFY_FAILED." >&2
    echo "      Fix: kubectl delete secret speedscale-certs -n $ns  (operator regenerates a consistent copy)" >&2
    fail=1
  fi
done

exit $fail
