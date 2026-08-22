# Yeongdo Reservation Crawler

## Provider

- Provider: `MUNI_WWW_YEONGDO_GO_KR_33400564`
- Name: Yeongdo integrated reservation courses
- URL: `https://www.yeongdo.go.kr/reserve/01785/01791.web`
- Crawler: `Crawler/generated_yaml/MUNI_WWW_YEONGDO_GO_KR_33400564.py`

## Structure

- The list page is a card list under `.list1f1t2b2 li.li1`.
- Detail links use `?amode=view&idx=<id>`.
- Detail pages contain `table.t3.ttvam` with course fields.
- Pagination uses `cpage`.

## Collected Fields

- `title`: list card title, split from `[branch] title`.
- `branch`: list title prefix such as `영도도서관`, `문화예술회관`.
- `address`: detail `교육장소` with Yeongdo district fallback.
- `period`: detail `교육기간`.
- `schedule_raw`: period plus detail `교육시간`.
- `target`: detail `모집대상`.
- `fee`: detail `수강료`.
- `material_note`: detail `준비물`.
- `status`: list button status normalized.
- `description`: detail table summary.
- `image_url`: list card image.
- `instructor`: detail `강사`.

## Verification

2026-06-08:

```text
python -m py_compile Crawler/generated_yaml/MUNI_WWW_YEONGDO_GO_KR_33400564.py
python -X utf8 Crawler/generated_yaml/MUNI_WWW_YEONGDO_GO_KR_33400564.py --limit 10 --max-pages 2 --timeout 35
python -X utf8 Crawler/generated_yaml/MUNI_WWW_YEONGDO_GO_KR_33400564.py --limit 10 --max-pages 2 --timeout 35 --save-db
```

Result:

- Collected: 10
- Saved: 10
- Quality: A / 100.0

## Notes

- Expired education periods are skipped by default.
- Detail page heading is not used for title because it can capture global navigation text; the list card title is cleaner.
