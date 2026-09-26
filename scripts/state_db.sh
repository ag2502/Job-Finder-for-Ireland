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
#
# `pull` starts from the committed snapshot only when the release does not exist. It used
# to do so whenever the download failed, for any reason, and the run then published the
# snapshot over the live state: on 2026-09-25 that erased every source the registry
# sweeps had found since 2026-09-06, and nothing reported it. A release that exists but
# cannot be read now fails the run, which leaves the state where it was.
set -euo pipefail

TAG="${JOBFINDER_STATE_TAG:-state}"
ASSET="jobfinder.db"
# A second copy, uploaded before the main asset is replaced. `--clobber` deletes the old
# asset before uploading the new one, so an upload that dies in between would otherwise
# leave the release with no state at all.
SPARE="jobfinder.spare.db"
DB="${JOBFINDER_STATE_PATH:-$PWD/jobfinder.db}"

looks_like_state() {
  python3 - "$1" <<'PY'
import sqlite3, sys
try:
    con = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
    n = con.execute("select count(*) from sources").fetchone()[0]
except sqlite3.Error as exc:
    sys.exit(f"not a crawler database: {exc}")
if n == 0:
    sys.exit("crawler database has no sources")
PY
}

download() {
  local name="$1" attempt
  for attempt in 1 2 3; do
    if gh release download "$TAG" --pattern "$name" --output "$DB" --clobber \
        && looks_like_state "$DB"; then
      echo "restored state from release '$TAG' asset $name ($(du -h "$DB" | cut -f1))"
      return 0
    fi
    echo "download of $name failed (attempt $attempt); retrying"
    sleep $((attempt * ${JOBFINDER_STATE_RETRY_SECONDS:-15}))
  done
  return 1
}

pull() {
  local view
  if view=$(gh release view "$TAG" --json assets --jq '.assets[].name' 2>&1); then
    if grep -qx "$ASSET" <<<"$view" && download "$ASSET"; then
      return
    fi
    if grep -qx "$SPARE" <<<"$view" && download "$SPARE"; then
      echo "::warning::main state asset unreadable; restored from $SPARE"
      return
    fi
    if [ "${JOBFINDER_STATE_BOOTSTRAP:-}" != "1" ]; then
      echo "::error::release '$TAG' exists but no state could be restored from it." \
        "Refusing to start from the snapshot, which would overwrite the live state." \
        "Set JOBFINDER_STATE_BOOTSTRAP=1 to rebuild deliberately."
      exit 1
    fi
  elif ! grep -qi "not found" <<<"$view"; then
    echo "::error::could not reach release '$TAG': $view"
    exit 1
  fi

  # First run, the release was deleted, or a deliberate rebuild. Seed from the snapshot
  # committed for the website rather than starting empty: it carries real
  # `first_seen_at` timestamps for the jobs it holds, so the history that drives close
  # decisions survives.
  if [ -f data/jobfinder.db ]; then
    cp data/jobfinder.db "$DB"
    echo "::warning::no state in release '$TAG'; bootstrapped from the committed snapshot"
  else
    echo "::warning::no release '$TAG' and no snapshot; starting from an empty database"
  fi
}

push() {
  if [ ! -f "$DB" ]; then
    echo "::error::no database at $DB to publish"
    exit 1
  fi
  looks_like_state "$DB"

  # `gh release create` fails if the tag exists, which is the normal case after the first
  # run, so the absence of the release is what decides whether to create it.
  if ! gh release view "$TAG" >/dev/null 2>&1; then
    gh release create "$TAG" \
      --title "Crawler state" \
      --notes "The crawler's SQLite database. Replaced by each successful run; not a source release." \
      --latest=false
    echo "created release '$TAG'"
  fi

  local spare_dir
  spare_dir=$(mktemp -d)
  cp "$DB" "$spare_dir/$SPARE"
  gh release upload "$TAG" "$spare_dir/$SPARE" --clobber
  rm -rf "$spare_dir"
  gh release upload "$TAG" "$DB" --clobber
  echo "published state to release '$TAG' ($(du -h "$DB" | cut -f1))"
}

case "${1:-}" in
  pull) pull ;;
  push) push ;;
  *) echo "usage: $0 {pull|push}" >&2; exit 2 ;;
esac
