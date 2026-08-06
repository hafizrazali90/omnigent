#!/usr/bin/env bash
# Loads the maintainer set from .github/MAINTAINER at main's tip, plus the
# owner of this repository when it is a fork.
#
# Always main, never the PR head SHA: otherwise a PR could edit
# MAINTAINER to grant itself a maintainer-gated waiver (e.g.
# skip-security-scan, skip-e2e-ui-test) without being merged.
# Defense-in-depth: a PR could still edit *this* workflow to drop
# `?ref=main`, so the remaining defense is `required_pull_request_reviews`
# in branch protection.
#
# MAINTAINER format: one bare username per line, with comments (`#`)
# and blank lines ignored.
#
# FORK OWNER -- on a fork, .github/MAINTAINER lists the *upstream* project's
# maintainers, none of whom can review or approve anything here. The account
# that owns the fork already controls its settings, branch protection and the
# base branch these trusted workflows run from, so it is a maintainer of the
# fork by definition. It is added to the list, and reported separately as
# `fork_owner` for gates that need the stronger "this exact account" identity.
# Trust comes from repository metadata read through the API by a base-branch
# job -- never from PR-controlled input (branch names, diff contents, the PR
# author field), so a PR cannot claim ownership it does not have. On the
# upstream repository `.fork` is false and nothing is added, leaving the
# original maintainer list and behavior untouched.
#
# Env in: GH_TOKEN, REPO
# Out:    `list=<space-separated usernames>` on $GITHUB_OUTPUT (empty
#         when MAINTAINER is missing or empty and this is not a fork).
#         `fork_owner=<username>` (empty when this repo is not a
#         user-owned fork).

set -euo pipefail

lower() { echo "$1" | tr '[:upper:]' '[:lower:]'; }

# --- Fork owner (repository metadata, not PR input) -----------------------
# Restricted to `User` owners: an organization login is never a PR author, so
# trusting it would grant nothing while widening the rule.
FORK_OWNER=""
set +e
REPO_JSON=$(gh api "repos/$REPO" 2>/dev/null)
RC=$?
set -e
if [[ $RC -eq 0 && -n "$REPO_JSON" ]]; then
  FORK_OWNER=$(jq -r 'if (.fork == true and .owner.type == "User")
                      then (.owner.login // "") else "" end' <<< "$REPO_JSON" 2>/dev/null || true)
  [[ "$FORK_OWNER" == "null" ]] && FORK_OWNER=""
else
  echo "::warning::Could not read repository metadata; fork-owner trust is unavailable."
fi

# --- Upstream maintainer list from .github/MAINTAINER@main ----------------
set +e
CONTENT_B64=$(gh api "repos/$REPO/contents/.github/MAINTAINER?ref=main" --jq '.content' 2>/dev/null)
RC=$?
set -e

USERS=""
if [[ $RC -ne 0 || -z "$CONTENT_B64" ]]; then
  echo "::warning::.github/MAINTAINER not found on main; only fork-owner trust (if any) applies."
else
  CONTENT=$(echo "$CONTENT_B64" | base64 -d)
  # `grep -v` exits 1 on no matches; wrap so the pipeline stays 0 under
  # pipefail and we reach the empty-list branch.
  USERS=$(echo "$CONTENT" | sed -E 's/#.*$//' | tr -s '[:space:]' '\n' | { grep -v '^$' || true; } | tr '\n' ' ')
  USERS="${USERS% }"
  if [[ -z "${USERS// /}" ]]; then
    USERS=""
    echo "::warning::.github/MAINTAINER on main has no entries."
  fi
fi

# --- Merge the fork owner in (deduplicated, case-insensitive) -------------
if [[ -n "$FORK_OWNER" ]]; then
  fo_lc=$(lower "$FORK_OWNER")
  already=false
  for u in $USERS; do
    [[ "$(lower "$u")" == "$fo_lc" ]] && already=true
  done
  if [[ "$already" != "true" ]]; then
    USERS="${USERS:+$USERS }$FORK_OWNER"
  fi
  echo "This repository is a fork; owner @$FORK_OWNER is trusted as its maintainer."
fi

echo "fork_owner=$FORK_OWNER" >> "$GITHUB_OUTPUT"
echo "list=$USERS" >> "$GITHUB_OUTPUT"

if [[ -z "${USERS// /}" ]]; then
  echo "::warning::No maintainers resolved; maintainer-gated waivers cannot be effective."
else
  echo "Loaded maintainers: $USERS"
fi
