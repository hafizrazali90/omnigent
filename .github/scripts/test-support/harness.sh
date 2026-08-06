#!/usr/bin/env bash
# Tiny fixture harness for the CI gate scripts. Source it, then per case:
#
#   new_case "description"
#   fixture repo.json <<'JSON'
#   ...
#   JSON
#   run bash .github/scripts/.../script.sh
#   assert_rc 0
#   assert_contains "PASS:"
#
# End with `summary`. Fully offline: `gh` and `curl` are stubs on PATH.

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
SUPPORT="$REPO_ROOT/.github/scripts/test-support"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

STUB_BIN="$TMP/bin"
mkdir -p "$STUB_BIN"
cp "$SUPPORT/stub-gh.sh" "$STUB_BIN/gh"
cp "$SUPPORT/stub-curl.sh" "$STUB_BIN/curl"
chmod +x "$STUB_BIN/gh" "$STUB_BIN/curl"
PATH="$STUB_BIN:$PATH"
export PATH

FAILURES=0
CASE=""
OUT=""
RC=0

new_case() {
  CASE="$1"
  echo
  echo "CASE: $CASE"
  FIX="$TMP/fixtures"
  rm -rf "$FIX"
  mkdir -p "$FIX"
  export GH_STUB_FIXTURES="$FIX"
  GITHUB_OUTPUT="$TMP/github_output"
  : > "$GITHUB_OUTPUT"
  export GITHUB_OUTPUT
  unset CURL_STUB_RC CURL_STUB_BODY
}

# fixture <name>  -- body on stdin
fixture() { cat > "$FIX/$1"; }

run() {
  set +e
  OUT=$("$@" 2>&1)
  RC=$?
  set -e
}

ok()  { printf '  PASS  %s\n' "$1"; }
bad() {
  printf '  FAIL  %s\n' "$1"
  printf '        rc=%s, output:\n%s\n' "$RC" "$OUT" | sed 's/^/        /'
  FAILURES=$((FAILURES + 1))
}

assert_rc() {
  if [[ "$RC" == "$1" ]]; then ok "exit $1"; else bad "expected exit $1, got $RC"; fi
}
assert_contains() {
  if [[ "$OUT" == *"$1"* ]]; then ok "says '$1'"; else bad "output missing '$1'"; fi
}
assert_not_contains() {
  if [[ "$OUT" != *"$1"* ]]; then ok "does not say '$1'"; else bad "output unexpectedly has '$1'"; fi
}
# Exact line match against $GITHUB_OUTPUT, e.g. `assert_output_line "list=a b"`.
assert_output_line() {
  if grep -qxF "$1" "$GITHUB_OUTPUT"; then
    ok "GITHUB_OUTPUT has '$1'"
  else
    bad "GITHUB_OUTPUT missing '$1' (got: $(tr '\n' '|' < "$GITHUB_OUTPUT"))"
  fi
}

summary() {
  echo
  if (( FAILURES == 0 )); then
    echo "All checks passed."
    exit 0
  fi
  echo "$FAILURES check(s) failed."
  exit 1
}
