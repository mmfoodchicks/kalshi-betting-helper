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
# DraftKings standings exports name every entrant (the owner, 2026-11-26: three were committed in
# September and had to be removed from the public tree). Refuse any staged file that is one -- by its
# name, or by DraftKings' header in its first 4 KB (gzipped too), whatever it is called. Code and
# docs (.py / .md) quote the header in fixtures and prose, so they are not read for it.
BAD=$(git diff --cached --name-only --diff-filter=ACMR | while IFS= read -r f; do
  case "$f" in
    *contest-standings*|*standings*.csv|*standings*.csv.gz|*standings*.zip) echo "$f"; continue ;;
    *.py|*.md|scripts/secret-scan.sh) continue ;;
  esac
  case "$f" in
    *.gz) H=$(git show ":$f" 2>/dev/null | gzip -dc 2>/dev/null | head -c 4096 | tr -d '\000' || true) ;;
    *) H=$(git show ":$f" 2>/dev/null | head -c 4096 | tr -d '\000' || true) ;;
  esac
  if printf '%s' "$H" | grep -q 'EntryId,EntryName'; then echo "$f"; fi
done || true)
if [ -n "$BAD" ]; then
  echo "SECRET SCAN: a DraftKings standings export is staged (it names every entrant) -- commit its summary, never the export:"
  echo "$BAD"
  exit 1
fi
echo "secret scan: clean"
