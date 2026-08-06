#!/usr/bin/env bash
# Decides the `Maintainer Approval` required check: does a maintainer stand
# behind this PR?
#
# Passes when EITHER:
#   1. The PR author is a maintainer, or
#   2. Some maintainer's latest decisive (non-COMMENTED) review is APPROVED.
#      Matches GitHub's UI semantics -- a later COMMENTED review does not
#      supersede an approval, but CHANGES_REQUESTED or DISMISSED does.
#
# The maintainer set comes from merge-ready/load-maintainers.sh, which reads
# .github/MAINTAINER at main's tip and adds a fork's owner. Called from a
# base-branch (pull_request_target) job that checks out nothing from the PR
# head, so a PR cannot edit this script or the list to approve itself.
#
# Env in:  GH_TOKEN, REPO, PR, MAINTAINERS (space-separated).
# Exit:    0 = a maintainer stands behind the PR; 1 = merge stays blocked.

set -euo pipefail

fail() { echo "::error::$1"; exit 1; }

if [[ -z "${MAINTAINERS// /}" ]]; then
  fail "No maintainers could be resolved (.github/MAINTAINER on main is missing or empty, and this repo has no trusted fork owner). Cannot approve."
fi

MAINTAINERS_LC=$(echo "$MAINTAINERS" | tr '[:upper:]' '[:lower:]')

AUTHOR=$(gh pr view "$PR" --repo "$REPO" --json author --jq '.author.login')
AUTHOR_LC=$(echo "$AUTHOR" | tr '[:upper:]' '[:lower:]')

# Case 1: author is a maintainer.
for m in $MAINTAINERS_LC; do
  if [[ "$m" == "$AUTHOR_LC" ]]; then
    echo "Author @$AUTHOR is a maintainer."
    exit 0
  fi
done

# Case 2: a maintainer's latest decisive review is APPROVED.
APPROVERS=$(gh api "repos/$REPO/pulls/$PR/reviews" --paginate \
  --jq '[.[] | select(.state != "COMMENTED")] | group_by(.user.login) | map(max_by(.submitted_at)) | .[] | select(.state == "APPROVED") | .user.login')
for u in $APPROVERS; do
  u_lc=$(echo "$u" | tr '[:upper:]' '[:lower:]')
  for m in $MAINTAINERS_LC; do
    if [[ "$m" == "$u_lc" ]]; then
      echo "Approved by maintainer @$u."
      exit 0
    fi
  done
done

# Case 3: nothing yet -- fail the check so merge stays blocked.
fail "Awaiting approval from a maintainer (one of: $MAINTAINERS)."
