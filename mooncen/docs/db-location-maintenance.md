# DB Location Maintenance

## 2026-06-11 EMART Branch Coordinate Audit

The EMART branch coordinates were audited because map output suggested incorrect branch locations.

### Finding

- EMART had 80 branches with coordinates populated.
- Most coordinates were valid.
- `스타필드시티명지점` was incorrectly mapped to the same address/coordinate as `스타필드시티위례점`.
- Existing generic geocoding scripts contain corrupted Korean query strings, so they should not be used for EMART coordinate refresh without review.

### Fix

- Added `tools/maintenance/audit_emart_branch_locations.py`.
- The script uses Kakao Local keyword search first, then Kakao address search as fallback.
- The script builds Korean EMART-specific queries from branch names and ignores the currently stored address as the primary source because the stored address may already be wrong.
- Dry-run found one safe update:
  - `스타필드시티명지점`
  - old coordinate: near `스타필드시티위례점`
  - new address: `부산광역시 강서구 명지국제6로 168`
  - new coordinate: `35.0931631, 128.9181554`
- The update was applied to the development DB.

### Commands

Dry-run:

```bash
python -u tools/maintenance/audit_emart_branch_locations.py --dry-run
```

Apply:

```bash
python -u tools/maintenance/audit_emart_branch_locations.py
```

Compile check:

```bash
python -m py_compile tools/maintenance/audit_emart_branch_locations.py
```

### Notes

- `트레이더스킨텍스점` was not updated because the map search returned an unrelated `흥덕점` candidate.
- Rows with `location_verified=false` are not automatically updated if the new candidate is within the mismatch threshold. This avoids overwriting apparently correct coordinates with lower-quality candidates.

## Kakao-only convergence queue

The maintained geocoder is `tools/maintenance/kakao_geocode_branches.py`.
Every non-dry-run attempt updates a bounded operational record on `branches`:

- `geocode_status`: `resolved`, `no_result`, `low_confidence`,
  `invalid_address`, `region_mismatch`, `quota_exhausted`, `request_error`, or
  `manual_review`
- `geocode_reason_code`, `geocode_attempt_count`, and
  `geocode_last_attempt_at`
- up to five non-secret `geocode_candidates`
- `geocode_next_retry_at` and a bounded `geocode_last_error`

The nightly crawler runs three bounded passes for active-course branches. The
first uses stored addresses only; the second uses a stored city/district and
rejects Kakao candidates outside that locality. The third selects only legacy
`GOOGLE*` coordinate provenance and replaces a verified match with Kakao
provenance. A normal no-result is a data outcome, while Kakao
authentication/request failure or quota exhaustion returns non-zero and fails
the maintenance step.

Reverify only historical Google provenance and converge successful matches to
Kakao:

```bash
python -X utf8 tools/maintenance/kakao_geocode_branches.py \
  --verify-existing --coordinate-source-prefix GOOGLE \
  --with-active-courses --limit 100 --retry-after-days 30
```

Review the dry run first by adding `--dry-run`. API credentials are read only
from the server environment and must never be passed on the command line.
