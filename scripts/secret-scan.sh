#!/bin/sh
# Pre-commit secret scan. Exits non-zero (and prints the offending lines) if a
# staged ADDED line carries a known-leaked key prefix or a credential-looking
# assignment. Run from the repo root:  sh scripts/secret-scan.sh
#
# The prefixes below are the two keys that leaked historically (an Odds API key
# and a SerpAPI key). They are recorded HERE, in the one file the scan skips,
# so that documenting the check elsewhere can never trip it -- a check that
# always fires is a check people learn to ignore.
set -e
PAT='7932f4caad|9f889f611a|api_key|apikey|password'
HITS=$(git diff --cached -- . ':(exclude)scripts/secret-scan.sh' \
       | grep -E '^\+' | grep -v '^+++' | grep -iE "$PAT" || true)
if [ -n "$HITS" ]; then
  echo "SECRET SCAN: possible credential in staged changes -- inspect before committing:"
  echo "$HITS"
  exit 1
fi
echo "secret scan: clean"
