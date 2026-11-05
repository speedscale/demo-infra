#!/usr/bin/env bash
set -euo pipefail
forward_pid=$1
forward_log=$2
shift 2

"$@" &
replay_pid=$!
cleanup() { kill "$replay_pid" 2>/dev/null || true; }
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

while kill -0 "$replay_pid" 2>/dev/null; do
  if ! kill -0 "$forward_pid" 2>/dev/null; then
    echo 'Replay stopped: its target port-forward exited; remaining load cannot reach the service.' >&2
    tail -20 "$forward_log" >&2 || true
    kill "$replay_pid" 2>/dev/null || true
    for attempt in {1..10}; do
      kill -0 "$replay_pid" 2>/dev/null || break
      sleep 1
    done
    kill -KILL "$replay_pid" 2>/dev/null || true
    wait "$replay_pid" 2>/dev/null || true
    exit 1
  fi
  sleep "${PROXYMOCK_FORWARD_CHECK_INTERVAL:-2}"
done
wait "$replay_pid"
