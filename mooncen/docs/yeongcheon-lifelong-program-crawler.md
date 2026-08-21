# Yeongcheon Lifelong Program Crawler

## Provider

- Provider: `MUNI_WWW_YC_GO_KR_54558363`
- Name: Yeongcheon lifelong learning program application
- URL: `https://www.yc.go.kr/edu/portal/academy/lecture/program/list.do?mId=0303000000`
- Crawler: `Crawler/generated_yaml/MUNI_WWW_YC_GO_KR_54558363.py`

## Structure

- List page uses `div.cardWrap` cards.
- Pagination is a POST form with hidden `page`.
- Detail link uses `data-keyset` with `idx`.
- Detail page is available through `view.do?mId=0303000000&idx=<idx>`.

## Collected Fields

- `title`: detail `강좌명`, with branch/status suffix removed.
- `branch`: card `course`.
- `address`: detail `강의 장소` when address-like, otherwise Yeongcheon fallback.
- `period`: detail `교육 기간`.
- `schedule_raw`: period plus detail `교육 시간`.
- `target`: detail `교육 대상`.
- `fee`, `material_fee`, `material_note`: detail fee fields.
- `status`: list/detail status normalized to `OPEN`, `SCHEDULED`, `CLOSED`.
- `description`: detail `강좌 정보` and `유의 사항`.
- `instructor`: detail `강사명`.

## Verification

2026-06-08:

```text
python -m py_compile Crawler/generated_yaml/MUNI_WWW_YC_GO_KR_54558363.py
python -X utf8 Crawler/generated_yaml/MUNI_WWW_YC_GO_KR_54558363.py --limit 10 --max-pages 2 --timeout 35
python -X utf8 Crawler/generated_yaml/MUNI_WWW_YC_GO_KR_54558363.py --limit 10 --max-pages 2 --timeout 35 --save-db
```

Result:

- Collected: 10
- Saved: 10
- Quality: A / 100.0
- Required field counts: 10/10 for title, branch, address, period, schedule, target, fee, status, description, raw URL, instructor.

## Notes

- Expired education periods are skipped by default.
- Use `--include-expired` only for debugging historical data.
