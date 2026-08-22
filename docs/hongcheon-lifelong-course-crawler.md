# Hongcheon Lifelong Course Crawler

## Provider

- Provider: `MUNI_WWW_HONGCHEON_GO_KR_F5083BE8`
- Name: Hongcheon lifelong learning general education
- URL: `https://www.hongcheon.go.kr/edu/selectCourseWebList.do?key=1196&srcEdu=&srcCategory=&srcStatus=&srcTitle=`
- Crawler: `Crawler/generated_yaml/MUNI_WWW_HONGCHEON_GO_KR_F5083BE8.py`

## Structure

- The list page is a table with 10 rows per page.
- Detail links use `courseWebView.do?key=1196&course=<id>`.
- Detail pages contain `table.bbs_default.view` with course metadata.

## Collected Fields

- `title`: detail `강좌명`.
- `branch`: detail `교육장소`.
- `address`: `강원특별자치도 홍천군 <교육장소>` fallback, or Hongcheon office fallback.
- `period`: detail `교육기간`.
- `schedule_raw`: period plus detail `교육시간`.
- `target`: detail `교육대상`.
- `fee`, `material_fee`, `material_note`: detail `수강료` and `재료비`.
- `status`: list status normalized to `OPEN`, `SCHEDULED`, `CLOSED`.
- `description`: detail `교육내용`.
- `instructor`: detail `강사명`.

## Verification

2026-06-08:

```text
python -m py_compile Crawler/generated_yaml/MUNI_WWW_HONGCHEON_GO_KR_F5083BE8.py
python -X utf8 Crawler/generated_yaml/MUNI_WWW_HONGCHEON_GO_KR_F5083BE8.py --limit 10 --max-pages 2 --timeout 35
python -X utf8 Crawler/generated_yaml/MUNI_WWW_HONGCHEON_GO_KR_F5083BE8.py --limit 10 --max-pages 2 --timeout 35 --save-db
```

Result:

- Collected: 10
- Saved: 10
- Quality: A / 100.0

## Notes

- Expired education periods are skipped by default.
- Some `교육장소` values are already address-like; they are kept as branch names to preserve map-level grouping.
