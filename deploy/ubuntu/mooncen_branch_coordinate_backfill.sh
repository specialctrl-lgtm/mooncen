#!/usr/bin/env bash
set -Eeuo pipefail

readonly APP_DIR=/opt/mooncen
readonly PYTHON_BIN="$APP_DIR/.venv/bin/python"
readonly GEOCODER="$APP_DIR/tools/maintenance/kakao_geocode_branches.py"
readonly VERIFIED_COPY="$APP_DIR/tools/maintenance/propagate_branch_locations.py"

total_budget="${KAKAO_GEOCODE_MAX_REQUESTS_PER_RUN:-1000}"
branch_limit="${CRAWLER_COORDINATE_BACKFILL_LIMIT:-100}"
request_delay="${CRAWLER_COORDINATE_BACKFILL_DELAY:-0.5}"
min_confidence="${CRAWLER_LOCATION_MIN_CONFIDENCE:-75}"

if [[ ! "$total_budget" =~ ^[0-9]+$ ]] || (( total_budget < 100 || total_budget > 100000 )); then
  echo "invalid KAKAO_GEOCODE_MAX_REQUESTS_PER_RUN" >&2
  exit 64
fi
if [[ ! "$branch_limit" =~ ^[0-9]+$ ]] || (( branch_limit < 1 || branch_limit > 1000000 )); then
  echo "invalid CRAWLER_COORDINATE_BACKFILL_LIMIT" >&2
  exit 64
fi
if [[ ! "$request_delay" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
  echo "invalid CRAWLER_COORDINATE_BACKFILL_DELAY" >&2
  exit 64
fi
if [[ ! "$min_confidence" =~ ^[0-9]+$ ]] || (( min_confidence < 0 || min_confidence > 100 )); then
  echo "invalid CRAWLER_LOCATION_MIN_CONFIDENCE" >&2
  exit 64
fi
if [[ ! -x "$PYTHON_BIN" || ! -f "$GEOCODER" || ! -f "$VERIFIED_COPY" ]]; then
  echo "MoonCen coordinate backfill runtime is incomplete" >&2
  exit 66
fi

address_budget=$((total_budget * 12 / 100))
course_address_budget=$((total_budget * 8 / 100))
stored_region_budget=$((total_budget * 30 / 100))
configured_locality_budget=$((total_budget * 38 / 100))
legacy_reverify_budget=$((
  total_budget
  - address_budget
  - course_address_budget
  - stored_region_budget
  - configured_locality_budget
))

run_pass() {
  local request_budget="$1"
  local retry_days="$2"
  local exit_code
  shift 2
  set +e
  "$PYTHON_BIN" -X utf8 "$GEOCODER" \
    --with-active-courses \
    --limit "$branch_limit" \
    --delay "$request_delay" \
    --min-confidence "$min_confidence" \
    --retry-after-days "$retry_days" \
    --max-requests "$request_budget" \
    "$@"
  exit_code=$?
  set -e
  if (( exit_code == 3 )); then
    partial_progress=1
    return 0
  fi
  return "$exit_code"
}

partial_progress=0

# Reuse only one verified, same-provider/name coordinate group when all stored
# address and region evidence is non-conflicting. This pass makes no map API
# request and the tool revalidates the source before each update.
"$PYTHON_BIN" -X utf8 "$VERIFIED_COPY" \
  --with-active-courses \
  --limit "$branch_limit"

# Precise, non-overlapping evidence paths run before progressively broader
# locality lookups. A failed result receives a future retry timestamp, so a
# later pass in this same invocation cannot immediately spend on it again.
run_pass "$address_budget" 30 --address-only
run_pass "$course_address_budget" 30 --course-address-only
run_pass "$stored_region_budget" 14 --region-keyword-only
run_pass "$configured_locality_budget" 30 --configured-locality-only
run_pass "$legacy_reverify_budget" 30 --verify-existing --coordinate-source-prefix GOOGLE

if (( partial_progress != 0 )); then
  exit 3
fi
