#!/usr/bin/env bash
# Fixtures for merge-ready/require-approval.sh (the `Maintainer Approval`
# check). Offline (stubbed gh).
# Run from the repo root: bash .github/scripts/merge-ready/require-approval.test.sh

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../test-support/harness.sh"

SCRIPT=".github/scripts/merge-ready/require-approval.sh"
export REPO="owner/repo" GH_TOKEN="stub" PR="4"

author() { fixture pr_view.json <<EOF
{"author": {"login": "$1"}}
EOF
}
no_reviews() { fixture pr_reviews.json <<<'[]'; }
review() {  # $1 = login, $2 = state
  fixture pr_reviews.json <<EOF
[{"user": {"login": "$1"}, "state": "$2", "submitted_at": "2026-01-01T00:00:00Z"}]
EOF
}

new_case "author is an upstream maintainer -> approved"
export MAINTAINERS="mateiz dbczumar"
author "mateiz"
no_reviews
run bash "$SCRIPT"
assert_rc 0
assert_contains "Author @mateiz is a maintainer."

new_case "a maintainer approved -> approved"
export MAINTAINERS="mateiz dbczumar"
author "outsider"
review "dbczumar" "APPROVED"
run bash "$SCRIPT"
assert_rc 0
assert_contains "Approved by maintainer @dbczumar."

new_case "no maintainer involvement -> blocked"
export MAINTAINERS="mateiz dbczumar"
author "outsider"
no_reviews
run bash "$SCRIPT"
assert_rc 1
assert_contains "Awaiting approval from a maintainer"

new_case "non-maintainer approval does not count -> blocked"
export MAINTAINERS="mateiz dbczumar"
author "outsider"
review "friend-of-outsider" "APPROVED"
run bash "$SCRIPT"
assert_rc 1
assert_contains "Awaiting approval from a maintainer"

new_case "maintainer's latest decisive review requests changes -> blocked"
export MAINTAINERS="mateiz"
author "outsider"
fixture pr_reviews.json <<'EOF'
[{"user": {"login": "mateiz"}, "state": "APPROVED", "submitted_at": "2026-01-01T00:00:00Z"},
 {"user": {"login": "mateiz"}, "state": "CHANGES_REQUESTED", "submitted_at": "2026-01-02T00:00:00Z"}]
EOF
run bash "$SCRIPT"
assert_rc 1
assert_contains "Awaiting approval from a maintainer"

# --- The fork case this change exists for --------------------------------
# On a fork, load-maintainers.sh appends the fork's owner, so the owner's own
# PR satisfies the check without ever appearing in .github/MAINTAINER.
new_case "fork owner (appended by load-maintainers) authors the PR -> approved"
export MAINTAINERS="mateiz dbczumar forkowner"
author "ForkOwner"
no_reviews
run bash "$SCRIPT"
assert_rc 0
assert_contains "is a maintainer."

new_case "fork contributor who is not the owner -> still blocked"
export MAINTAINERS="mateiz dbczumar forkowner"
author "mallory"
no_reviews
run bash "$SCRIPT"
assert_rc 1
assert_contains "Awaiting approval from a maintainer"

new_case "no maintainers resolvable at all -> blocked, not silently open"
export MAINTAINERS=""
author "anyone"
run bash "$SCRIPT"
assert_rc 1
assert_contains "No maintainers could be resolved"

summary
