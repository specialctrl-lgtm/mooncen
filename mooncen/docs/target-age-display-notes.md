# Target Age Display Notes

## 2026-05-28

The course target label shown in the frontend is built from two sources:

- normalized database fields: `target_age_group`, `target_min_age`, `target_max_age`
- raw crawler text: `target`

The duplicate display such as `유아, 만 4세, 24개월, 4세` happened because the UI extracted every age-looking phrase from the raw `target` text and appended it next to the age group. When a crawler or AI pass had already normalized the same value into `target_min_age` and `target_max_age`, the raw text could still contain overlapping labels.

Frontend display now prefers normalized numeric age fields first. Raw `target` age text is only used when normalized age fields are missing. Equivalent labels such as `만 4세` and `4세` are deduplicated, and adult courses do not show month-based ages.

## 2026-05-28 Display Format

Target labels now use a fixed format based on normalized month fields:

- both min and max: `age_group, min_age~max_age (min_month~max_month개월)`
- min only: `age_group, min_age~ (min_month개월~)`
- max only: `age_group, ~max_age (~max_month개월)`
- adult: `성인`

Example: `TODDLER + 48~72 months` is displayed as `유아, 만 4세~만 6세 (48~72개월)`.
