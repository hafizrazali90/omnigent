#!/usr/bin/env bash
# Fake `gh` for the CI gate-script fixtures. Serves canned JSON from
# $GH_STUB_FIXTURES and applies `--jq` with the real jq, mirroring gh's output.
#
# A call with no matching fixture is a hard error, not an empty result: that is
# what makes the fixtures able to prove a script does NOT read some source
# (e.g. that fork-owner trust never consults PR-controlled data).

set -euo pipefail

FIX="${GH_STUB_FIXTURES:?GH_STUB_FIXTURES not set}"

sub="${1:-}"; shift || true
endpoint=""
jqexpr=""

case "$sub" in
  api) endpoint="${1:-}"; shift || true ;;
  pr)  shift || true ;;   # drop the `view` verb; the PR number falls through
  *)   echo "gh stub: unsupported subcommand '$sub'" >&2; exit 1 ;;
esac

while [[ $# -gt 0 ]]; do
  case "$1" in
    --jq|-q)       jqexpr="$2"; shift 2 ;;
    --json|--repo) shift 2 ;;
    *)             shift ;;
  esac
done

serve() {
  local f="$FIX/$1"
  if [[ ! -f "$f" ]]; then
    echo "gh stub: no fixture '$1' for '$sub $endpoint'" >&2
    exit 1
  fi
  cat "$f"
}

if [[ "$sub" == "pr" ]]; then
  out=$(serve pr_view.json)
else
  case "$endpoint" in
    */contents/.github/MAINTAINER*)
      if [[ ! -f "$FIX/MAINTAINER" ]]; then
        echo "gh stub: no MAINTAINER fixture" >&2
        exit 1
      fi
      out=$(jq -n --arg c "$(base64 < "$FIX/MAINTAINER")" '{content: $c}')
      ;;
    */pulls/*/files*)   out=$(serve pr_files.json) ;;
    */pulls/*/reviews*) out=$(serve pr_reviews.json) ;;
    */pulls/*)          out=$(serve pr.json) ;;
    repos/*)            out=$(serve repo.json) ;;
    *) echo "gh stub: unexpected endpoint '$endpoint'" >&2; exit 1 ;;
  esac
fi

if [[ -n "$jqexpr" ]]; then
  jq -r "$jqexpr" <<< "$out"
else
  printf '%s\n' "$out"
fi
