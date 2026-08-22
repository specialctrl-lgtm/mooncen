# Busan Donggu Reservation Crawler

## Provider

- Provider: `BUSAN_DONGGU_RESERVATION`
- Name: Busan Dong-gu integrated reservation
- URL: `https://www.bsdonggu.go.kr/reserve/index.donggu`
- Crawler: `Crawler/generated_yaml/BUSAN_DONGGU_RESERVATION.py`

## Structure

- The integrated reservation home page exposes active/recent courses across categories in `table.table` rows.
- Category list pages use `.bbs_ltype2 > dl`.
- Detail pages are linked by `data_Sid` and contain a `table.bbs_default.view` table.
- The crawler prioritizes the integrated home table so public reservation, library, English library, information education, and lifelong-learning courses can be collected together.

## Collected Fields

- `title`: detail `강좌명`.
- `branch`: detail `교육장소`, or category/default fallback.
- `address`: detail `교육장소주소` when present, otherwise `부산광역시 동구 <교육장소>` or district-office fallback.
- `period`: detail `교육시작일 ~ 교육종료일`.
- `schedule_raw`: period plus detail `교육시간`.
- `target`: detail `교육대상`.
- `fee`, `material_fee`, `material_note`: detail `기타경비` or description fallback such as `수강료`.
- `status`: list status normalized to `OPEN`, `SCHEDULED`, `CLOSED`.
- `description`: detail `강좌내용`.

## Verification

2026-06-08:

```text
python -m py_compile Crawler/generated_yaml/BUSAN_DONGGU_RESERVATION.py
python -X utf8 Crawler/generated_yaml/BUSAN_DONGGU_RESERVATION.py --limit 10 --max-pages 5 --timeout 35
python -X utf8 Crawler/generated_yaml/BUSAN_DONGGU_RESERVATION.py --limit 10 --max-pages 5 --timeout 35 --save-db
```

Result:

- Collected: 10
- Saved: 10
- Quality: A / 94.0

## Notes

- Expired education periods are skipped by default.
- Some detail pages leave the fee field empty; the crawler falls back to fee text in `강좌내용` when available.
