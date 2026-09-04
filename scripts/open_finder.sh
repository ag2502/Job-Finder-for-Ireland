#!/usr/bin/env bash
#
# Launch the Dublin Job Finder interface and open it in a browser.
#
# Safe to run repeatedly: if the server is already up on the port it just opens the
# browser rather than starting a second copy.
#
# Usage: scripts/open_finder.sh [port]

set -uo pipefail

PORT="${1:-8000}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT/job"
LOG="$ROOT/.jobfinder-server.log"
URL="http://127.0.0.1:$PORT"

cd "$ROOT" || exit 1

if [[ ! -x "$VENV/bin/python" ]]; then
  echo "ERROR: virtualenv not found at $VENV"
  echo "Create it, then: source job/bin/activate && uv pip install -e '.[dev]'"
  exit 1
fi

PY="$VENV/bin/python"

is_up() { curl -sf -o /dev/null --max-time 2 "$URL/privacy" 2>/dev/null; }

if is_up; then
  echo "Already running at $URL"
else
  # A stale process may hold the port without serving; clear only our own.
  pkill -f "uvicorn jobfinder.web.app.*--port $PORT" 2>/dev/null
  sleep 1

  echo "Starting server on port $PORT..."
  nohup "$PY" -m uvicorn jobfinder.web.app:app \
    --host 127.0.0.1 --port "$PORT" --log-level warning \
    >"$LOG" 2>&1 &

  for _ in $(seq 1 20); do
    is_up && break
    sleep 1
  done

  if ! is_up; then
    echo "ERROR: server did not come up. Last lines of $LOG:"
    tail -15 "$LOG"
    exit 1
  fi
  echo "Started."
fi

# Report what the searcher will actually find, so an empty database is obvious
# rather than looking like a broken page.
"$PY" - <<'PYEOF'
from jobfinder.core.db import session_scope
from jobfinder.core.models import Company, JobPosting, JobStatus
from sqlalchemy import func, select

try:
    with session_scope() as s:
        dublin = s.scalar(
            select(func.count()).select_from(JobPosting).where(
                JobPosting.status == JobStatus.ACTIVE, JobPosting.is_dublin.is_(True)
            )
        ) or 0
        companies = s.scalar(select(func.count()).select_from(Company)) or 0
        interns = s.scalar(
            select(func.count()).select_from(JobPosting).where(
                JobPosting.status == JobStatus.ACTIVE,
                JobPosting.is_dublin.is_(True),
                JobPosting.is_internship.is_(True),
            )
        ) or 0

    print(f"{dublin:,} active Dublin jobs from {companies} employers ({interns} internships)")
    if dublin == 0:
        print("Database is empty - run:  jobfinder seed && jobfinder crawl   (~9 min)")
except Exception as exc:  # noqa: BLE001 - reporting only, must not block the launch
    print(f"(could not read database: {exc})")
PYEOF

echo "$URL"

case "$(uname -s)" in
  Darwin) open "$URL" 2>/dev/null ;;
  Linux)  xdg-open "$URL" >/dev/null 2>&1 ;;
esac
