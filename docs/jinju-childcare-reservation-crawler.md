# Jinju Childcare Reservation Crawler

## Provider

- Provider: `MUNI_WWW_JINJU_GO_KR_5DF28B13`
- Name: Jinju integrated reservation childcare center programs
- URL: `https://www.jinju.go.kr/yeyak/08870/08882/09650.web`
- Crawler: `Crawler/generated_yaml/MUNI_WWW_JINJU_GO_KR_5DF28B13.py`

## Structure

- The site exposes the childcare center menus under `/yeyak/08870/08882/`.
- The requested `li.hasSub` behavior is implemented by discovering those menu links and cycling them.
- Course rows are card items under `.cp31edu1list1 li.li1`.
- Pagination uses `cpage`.
- The card itself contains the required fields, so a separate detail parser is not required.

## Collected Fields

- `title`: `.tg1 strong.t1`.
- `branch`: fixed `진주시 육아종합지원센터`.
- `category_raw`: discovered menu label plus `교육구분`.
- `period`: `교육기간`.
- `schedule_raw`: period plus `요일시간`.
- `target`: `신청대상`.
- `fee`: `수강료`.
- `status`: `접수중`, `홍보중`, `정원마감`, `접수마감` normalized.
- `capacity_current`, `capacity_total`, `waitlist_total`: parsed from `정원/접수인원/대기자정원` and `신청현황`.
- `description`: card field summary.
- `image_url`: course image from `ImagePrint.do`.

## Verification

2026-06-08:

```text
python -m py_compile Crawler/generated_yaml/MUNI_WWW_JINJU_GO_KR_5DF28B13.py
python -X utf8 Crawler/generated_yaml/MUNI_WWW_JINJU_GO_KR_5DF28B13.py --limit 10 --max-pages 2 --timeout 35
python -X utf8 Crawler/generated_yaml/MUNI_WWW_JINJU_GO_KR_5DF28B13.py --limit 10 --max-pages 2 --timeout 35 --save-db
```

Result:

- Discovered menus: 16
- Collected: 10
- Saved: 10
- Quality: A / 100.0

## Notes

- Expired education periods are skipped by default.
- The menu labels are categories, not physical branches, so branch is kept fixed to avoid false map points.
