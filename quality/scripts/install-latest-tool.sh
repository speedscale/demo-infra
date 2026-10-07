#!/usr/bin/env bash
set -euo pipefail

tool="${1:?Usage: install-latest-tool.sh speedctl|proxymock}"
case "$tool" in
  speedctl) installer_url=https://downloads.speedscale.com/speedctl/install ;;
  proxymock) installer_url=https://downloads.speedscale.com/proxymock/install-proxymock ;;
  *) echo "Unsupported tool: $tool" >&2; exit 1 ;;
esac

install_root="${INSTALLROOT:-$HOME/.speedscale}"
installer="$(mktemp)"
trap 'rm -f "$installer"' EXIT
curl --fail --silent --show-error --location --retry 3 --retry-all-errors \
  --connect-timeout 15 --max-time 120 "$installer_url" -o "$installer"
# No version argument selects the publisher's latest binary and verifies its checksum.
INSTALLROOT="$install_root" bash "$installer"
version="$("$install_root/$tool" version --client)"
printf '%s\n' "$version"
if [[ -n "${GITHUB_PATH:-}" ]]; then
  printf '%s\n' "$install_root" >> "$GITHUB_PATH"
fi
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  printf '\nLatest %s installed from downloads.speedscale.com:\n\n```text\n%s\n```\n' \
    "$tool" "$version" >> "$GITHUB_STEP_SUMMARY"
fi
