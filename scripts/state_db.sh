#!/usr/bin/env bash
#
# Move the crawler's database between a GitHub Release and the workspace.
#
# The pipeline needs somewhere durable to keep state between runs. The reconciler decides
# whether a job is still open by comparing what a crawl saw against what the last crawl
# saw — `consecutive_misses`, `first_seen_at`, the per-source volume baseline — so a run
# that starts from an empty database cannot tell a closed job from one it has never seen,
# and would re-open everything it had previously closed.
#
# That used to be hosted Postgres, which is a strange thing to rent for this workload: one
# writer, four batch writes a day, no concurrent access, and 38 MB of data. It was also
# what took the project down — the free tier's transfer quota ran out mid-month and every
# part of the system that touched the database stopped at once.
#
# A Release asset is a better fit. GitHub already hosts the repository, the runner already
# has a token that can write to it, transfer is not metered, and the access pattern the
# database actually has — fetch whole file, mutate locally, put whole file back — is
# exactly what an asset supports. There is no server to be down and no quota to exhaust.
#
# Safe because the two workflows that call this share a concurrency group, so only one
# run holds the database at a time. `push` is deliberately called only after a successful
# crawl: a run that dies halfway leaves the previous state untouched rather than
# overwriting it with a partial one.
set -euo pipefail

TAG="${JOBFINDER_STATE_TAG:-state}"
ASSET="jobfinder.db"
DB="${JOBFINDER_STATE_PATH:-$PWD/jobfinder.db}"

pull() {
  if gh release download "$TAG" --pattern "$ASSET" --output "$DB" --clobber 2>/dev/null; then
    echo "restored state from release '$TAG' ($(du -h "$DB" | cut -f1))"
    return
  fi

  # First run, or the release was deleted. Seed from the snapshot committed for the
  # website rather than starting empty: it carries real `first_seen_at` timestamps for
  # the jobs it holds, so the history that drives close decisions survives. It covers
  # only Dublin and remote postings, so the first crawl after a bootstrap re-registers
  # everything else as newly seen — a one-off cost, and only on a cold start.
  if [ -f data/jobfinder.db ]; then
    cp data/jobfinder.db "$DB"
    echo "no release '$TAG' yet; bootstrapped from the committed snapshot"
  else
    echo "no release '$TAG' and no snapshot; starting from an empty database"
  fi
}

push() {
  if [ ! -f "$DB" ]; then
    echo "::error::no database at $DB to publish"
    exit 1
  fi

  # `gh release create` fails if the tag exists, which is the normal case after the first
  # run, so the absence of the release is what decides whether to create it.
  if ! gh release view "$TAG" >/dev/null 2>&1; then
    gh release create "$TAG" \
      --title "Crawler state" \
      --notes "The crawler's SQLite database. Replaced by each successful run; not a source release." \
      --latest=false
    echo "created release '$TAG'"
  fi

  gh release upload "$TAG" "$DB" --clobber
  echo "published state to release '$TAG' ($(du -h "$DB" | cut -f1))"
}

case "${1:-}" in
  pull) pull ;;
  push) push ;;
  *) echo "usage: $0 {pull|push}" >&2; exit 2 ;;
esac
