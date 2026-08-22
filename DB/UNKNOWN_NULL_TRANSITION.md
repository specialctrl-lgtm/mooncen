# Unknown/NULL Transition

`NULL` is the canonical representation for a value the source did not publish.
Numeric zero is reserved for an explicit source value, such as a clearly marked
free course. An absent schedule cadence is `NULL`, not `WEEKLY`.

## Applied for new writes

- `courses.fee` and `courses.material_fee` no longer have a zero default.
- `courses.schedule_frequency` no longer has a `WEEKLY` default.
- The fresh schema and versioned migration both enforce those defaults.

## Existing data cleanup

Existing zero values are deliberately not mass-converted because a stored zero
can be either explicit free or historical unknown. Backfill only rows for which
`raw_fields`, source text, or crawler-specific evidence proves the distinction:

1. Mark explicit free values as zero and store the source evidence in `raw_fields`.
2. Convert unsupported historical defaults to `NULL` provider by provider.
3. Re-run API/filter regression tests after each provider batch.
4. Validate the `NOT VALID` constraints from
   `20260710_001_staging_integrity.sql` after legacy date/lifecycle debt is fixed.

Suggested validation commands:

```sql
ALTER TABLE courses VALIDATE CONSTRAINT chk_course_date_order;
ALTER TABLE courses VALIDATE CONSTRAINT chk_course_apply_date_order;
ALTER TABLE courses VALIDATE CONSTRAINT chk_course_capacity_nonnegative;
ALTER TABLE courses VALIDATE CONSTRAINT chk_course_capacity_remaining;
ALTER TABLE courses VALIDATE CONSTRAINT chk_course_lifecycle;
ALTER TABLE courses VALIDATE CONSTRAINT chk_course_seen_order;
ALTER TABLE courses VALIDATE CONSTRAINT chk_course_url_shape;
ALTER TABLE courses VALIDATE CONSTRAINT courses_branch_id_fkey;
```

Do not infer `fee = 0` from an empty string, HTTP failure, or missing selector.
