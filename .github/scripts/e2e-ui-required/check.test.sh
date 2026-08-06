#!/usr/bin/env bash
# Fixtures for e2e-ui-required/check.sh. Offline: gh and the LLM gateway (curl)
# are both stubbed.
# Run from the repo root: bash .github/scripts/e2e-ui-required/check.test.sh

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../test-support/harness.sh"

SCRIPT=".github/scripts/e2e-ui-required/check.sh"
export REPO="owner/repo" GH_TOKEN="stub" PR="4"

# Judge configuration: all three settings, or none.
judge_on()  { export OPENAI_BASE_URL="https://gw.test/v1" OPENAI_API_KEY="k" E2E_UI_JUDGE_MODEL="judge-model"; }
judge_off() { unset OPENAI_BASE_URL OPENAI_API_KEY E2E_UI_JUDGE_MODEL; }
verdict()   { # $1 = true|false, $2 = reason
  export CURL_STUB_BODY="{\"choices\":[{\"message\":{\"content\":\"{\\\"needs_test\\\": $1, \\\"reason\\\": \\\"$2\\\"}\"}}]}"
}

author()  { fixture pr_view.json <<EOF
{"author": {"login": "$1"}, "title": "Some PR"}
EOF
}
labels()  { fixture pr.json <<EOF
{"labels": [$1]}
EOF
}
no_label()   { labels ''; }
skip_label() { labels '{"name": "skip-e2e-ui-test"}'; }
no_reviews() { fixture pr_reviews.json <<<'[]'; }
approved_by() { fixture pr_reviews.json <<EOF
[{"user": {"login": "$1"}, "state": "APPROVED", "submitted_at": "2026-01-01T00:00:00Z"}]
EOF
}

# Changed-file sets. The `patch` fields feed the judge prompt builder.
files_web_only() { fixture pr_files.json <<'EOF'
[{"status": "modified", "filename": "web/src/Chat.tsx", "patch": "@@\n+ new button"}]
EOF
}
files_web_and_test() { fixture pr_files.json <<'EOF'
[{"status": "modified", "filename": "web/src/Chat.tsx", "patch": "@@\n+ new button"},
 {"status": "added", "filename": "tests/e2e_ui/chat/test_new_button.py", "patch": "@@\n+ def test_new_button(): ..."}]
EOF
}
files_web_and_deleted_test() { fixture pr_files.json <<'EOF'
[{"status": "modified", "filename": "web/src/Chat.tsx", "patch": "@@\n+ new button"},
 {"status": "removed", "filename": "tests/e2e_ui/chat/test_old.py", "patch": "@@\n- def test_old(): ..."}]
EOF
}
files_no_web() { fixture pr_files.json <<'EOF'
[{"status": "modified", "filename": "omnigent/server/app.py", "patch": "@@\n+ pass"}]
EOF
}

# ===========================================================================
# Unchanged behavior: no web change, and the LLM judge path
# ===========================================================================

new_case "no web/** change -> passes without needing any judge configuration"
judge_off
export MAINTAINERS="mateiz" TRUSTED_FORK_OWNER=""
files_no_web
run bash "$SCRIPT"
assert_rc 0
assert_contains "touches no web/** files"

new_case "judge configured, verdict no-test-needed -> passes via the judge"
judge_on
verdict false "pure refactor"
export MAINTAINERS="mateiz" TRUSTED_FORK_OWNER=""
files_web_only
author "outsider"
run bash "$SCRIPT"
assert_rc 0
assert_contains "e2e_ui judge -> no test required"

new_case "judge configured, verdict test-needed, no waiver -> blocked"
judge_on
verdict true "adds a new user-facing button"
export MAINTAINERS="mateiz" TRUSTED_FORK_OWNER=""
files_web_only
author "outsider"
no_label
run bash "$SCRIPT"
assert_rc 1
assert_contains "without a tests/e2e_ui/** test that covers it"

new_case "judge configured on a fork -> still the judge, not the fallback"
judge_on
verdict true "adds a new user-facing button"
export MAINTAINERS="forkowner" TRUSTED_FORK_OWNER="forkowner"
files_web_and_test
author "forkowner"
no_label
run bash "$SCRIPT"
assert_rc 1
assert_contains "e2e_ui judge -> test required"
assert_not_contains "no e2e_ui judge is configured"

new_case "judge configured but the gateway is unreachable -> fails closed"
judge_on
export CURL_STUB_RC=7
export MAINTAINERS="mateiz" TRUSTED_FORK_OWNER=""
files_web_only
author "outsider"
run bash "$SCRIPT"
assert_rc 1
assert_contains "Could not reach the e2e_ui judge"

# ===========================================================================
# Judge configuration hygiene
# ===========================================================================

new_case "partially configured judge -> loud failure, never a silent downgrade"
judge_off
export E2E_UI_JUDGE_MODEL="judge-model"     # model set, credentials missing
export MAINTAINERS="forkowner" TRUSTED_FORK_OWNER="forkowner"
files_web_and_test
author "forkowner"
run bash "$SCRIPT"
assert_rc 1
assert_contains "only partially configured"
assert_contains "OPENAI_BASE_URL"
assert_not_contains "PASS:"

new_case "no judge configured off a fork -> unchanged upstream expectation"
judge_off
export MAINTAINERS="mateiz" TRUSTED_FORK_OWNER=""
files_web_and_test
author "mateiz"
run bash "$SCRIPT"
assert_rc 1
assert_contains "e2e_ui judge is not configured"

# ===========================================================================
# The judge-free fork-owner fallback
# ===========================================================================

new_case "fork owner, web change WITH an e2e_ui test -> passes deterministically"
judge_off
export MAINTAINERS="mateiz forkowner" TRUSTED_FORK_OWNER="forkowner"
files_web_and_test
author "forkowner"
run bash "$SCRIPT"
assert_rc 0
assert_contains "ships an added/updated tests/e2e_ui/** test"

new_case "fork owner, capitalisation differs -> still recognised"
judge_off
export MAINTAINERS="ForkOwner" TRUSTED_FORK_OWNER="ForkOwner"
files_web_and_test
author "forkowner"
run bash "$SCRIPT"
assert_rc 0
assert_contains "ships an added/updated tests/e2e_ui/** test"

new_case "fork owner, web change WITHOUT an e2e_ui test -> fails closed"
judge_off
export MAINTAINERS="forkowner" TRUSTED_FORK_OWNER="forkowner"
files_web_only
author "forkowner"
no_label
run bash "$SCRIPT"
assert_rc 1
assert_contains "without adding or updating any tests/e2e_ui/** test"

new_case "fork owner, e2e_ui test only DELETED -> not coverage, fails closed"
judge_off
export MAINTAINERS="forkowner" TRUSTED_FORK_OWNER="forkowner"
files_web_and_deleted_test
author "forkowner"
no_label
run bash "$SCRIPT"
assert_rc 1
assert_contains "without adding or updating any tests/e2e_ui/** test"

new_case "fork owner, no e2e_ui test but owner-applied waiver -> passes"
judge_off
export MAINTAINERS="forkowner" TRUSTED_FORK_OWNER="forkowner"
files_web_only
author "forkowner"
skip_label
run bash "$SCRIPT"
assert_rc 0
assert_contains "waiver effective -- author @forkowner is a maintainer"

# ===========================================================================
# The fallback must not be reachable by an untrusted contributor
# ===========================================================================

new_case "untrusted contributor adds an e2e_ui test -> fallback does NOT apply"
judge_off
export MAINTAINERS="forkowner" TRUSTED_FORK_OWNER="forkowner"
files_web_and_test
author "mallory"
no_label
run bash "$SCRIPT"
assert_rc 1
assert_contains "limited to the fork owner"
assert_not_contains "PASS:"

new_case "untrusted contributor self-applies the skip label -> still blocked"
judge_off
export MAINTAINERS="forkowner" TRUSTED_FORK_OWNER="forkowner"
files_web_and_test
author "mallory"
skip_label
no_reviews
run bash "$SCRIPT"
assert_rc 1
assert_contains "is set but not effective"

new_case "untrusted contributor, waiver honoured only once the owner approves"
judge_off
export MAINTAINERS="forkowner" TRUSTED_FORK_OWNER="forkowner"
files_web_and_test
author "mallory"
skip_label
approved_by "forkowner"
run bash "$SCRIPT"
assert_rc 0
assert_contains "approved by maintainer @forkowner"

# Ownership comes from repository metadata (TRUSTED_FORK_OWNER, produced by the
# base-branch load-maintainers.sh) -- never from the PR author field. If the
# gate ever trusted the author instead, this case would pass and so fail here.
new_case "author field cannot confer ownership"
judge_off
export MAINTAINERS="forkowner" TRUSTED_FORK_OWNER="forkowner"
files_web_and_test
author "forkowner-impersonator"
no_label
run bash "$SCRIPT"
assert_rc 1
assert_contains "limited to the fork owner"

summary
