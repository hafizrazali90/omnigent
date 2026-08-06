#!/usr/bin/env bash
# Fake `curl` for the CI gate-script fixtures: replays $CURL_STUB_BODY, or
# exits with $CURL_STUB_RC to simulate a gateway/transport failure.

set -euo pipefail

rc="${CURL_STUB_RC:-0}"
if [[ "$rc" -ne 0 ]]; then
  exit "$rc"
fi
printf '%s' "${CURL_STUB_BODY:-}"
