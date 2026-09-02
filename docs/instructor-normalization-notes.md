# Instructor Normalization Notes

## 2026-05-29

Crawler instructor values are normalized to store names only.

Problem:

- Homeplus detail/list values were stored as strings like `유정민 강사님 소개`.
- The card already renders `강사` as the `dt` label, so storing the label/description text in `instructor` produced duplicated or noisy output.

Fix:

- Added `utils.clean_instructor_name()`.
- Homeplus, Emart, Lotte, YAML source, and municipal YAML crawlers now normalize instructor values before saving.
- The normalizer removes leading labels like `강사`, `강사명`, and trailing phrases like `강사님 소개`, `강사님`, `강사`, `소개`, and `프로필`.
- Placeholder values such as `미정` are stored as `NULL`.

Backfill:

- Added `tools/maintenance/normalize_instructors.py`.
- Local HOMEPLUS rows were backfilled:
  - scanned `6,196`
  - updated `6,193`
  - verification: `0` remaining HOMEPLUS instructor values containing `강사` or `소개`.
