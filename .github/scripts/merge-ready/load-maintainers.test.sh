#!/usr/bin/env bash
# Fixtures for merge-ready/load-maintainers.sh. Offline (stubbed gh).
# Run from the repo root: bash .github/scripts/merge-ready/load-maintainers.test.sh

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../test-support/harness.sh"

SCRIPT=".github/scripts/merge-ready/load-maintainers.sh"
export REPO="owner/repo" GH_TOKEN="stub"

upstream_maintainers() {
  fixture MAINTAINER <<'EOF'
# Maintainers of omnigent-ai/omnigent
mateiz
dbczumar
EOF
}

repo_meta() {  # $1 = fork (true/false), $2 = owner login, $3 = owner type
  fixture repo.json <<EOF
{"fork": $1, "owner": {"login": "$2", "type": "$3"}}
EOF
}

# --- Upstream: nothing changes -------------------------------------------
new_case "upstream repo (not a fork) -> original list, no fork owner"
upstream_maintainers
repo_meta false "omnigent-ai" "Organization"
run bash "$SCRIPT"
assert_rc 0
assert_output_line "list=mateiz dbczumar"
assert_output_line "fork_owner="

# --- Fork: the owner becomes a maintainer of the fork --------------------
new_case "user-owned fork -> owner appended, reported as fork_owner"
upstream_maintainers
repo_meta true "forkowner" "User"
run bash "$SCRIPT"
assert_rc 0
assert_output_line "list=mateiz dbczumar forkowner"
assert_output_line "fork_owner=forkowner"

new_case "fork owner already in MAINTAINER -> not duplicated"
fixture MAINTAINER <<'EOF'
mateiz
ForkOwner
EOF
repo_meta true "forkowner" "User"
run bash "$SCRIPT"
assert_rc 0
assert_output_line "list=mateiz ForkOwner"
assert_output_line "fork_owner=forkowner"

new_case "org-owned fork -> org login is not trusted as an author"
upstream_maintainers
repo_meta true "some-org" "Organization"
run bash "$SCRIPT"
assert_rc 0
assert_output_line "list=mateiz dbczumar"
assert_output_line "fork_owner="

new_case "fork with no MAINTAINER file -> fork owner alone keeps it usable"
repo_meta true "forkowner" "User"
run bash "$SCRIPT"
assert_rc 0
assert_output_line "list=forkowner"
assert_output_line "fork_owner=forkowner"

new_case "no MAINTAINER and not a fork -> empty list, warns"
repo_meta false "omnigent-ai" "Organization"
run bash "$SCRIPT"
assert_rc 0
assert_output_line "list="
assert_output_line "fork_owner="
assert_contains "::warning::No maintainers resolved"

# --- Trust must come from repo metadata, never from PR-controlled data ----
# The gh stub errors on any fixture it was not given. Only repo.json and
# MAINTAINER exist here, so if the script ever reached for PR data (author,
# branch, diff) to decide ownership, this case would fail instead of pass.
new_case "ownership is read from repo metadata only, never from PR data"
upstream_maintainers
repo_meta true "forkowner" "User"
run bash "$SCRIPT"
assert_rc 0
assert_not_contains "no fixture"
assert_output_line "fork_owner=forkowner"

# A repo whose metadata says it is not a fork gets no owner trust, even though
# an attacker-supplied login is available elsewhere in the event payload.
new_case "non-fork metadata -> no owner trust regardless of other inputs"
upstream_maintainers
repo_meta false "forkowner" "User"
run bash "$SCRIPT"
assert_rc 0
assert_output_line "list=mateiz dbczumar"
assert_output_line "fork_owner="

summary
