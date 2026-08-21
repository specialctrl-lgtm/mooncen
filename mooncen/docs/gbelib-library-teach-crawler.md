# GBELIB 도서관 강좌 크롤러

## 대상

- Provider: `MUNI_WWW_GBELIB_KR_04DB1B82`
- Site: 경상북도교육청 영덕도서관
- Base URL: `https://www.gbelib.kr/yd/module/teach/index.do?menu_idx=173&searchCate1=18`
- Parser: `gbelib_library_teach`

## 수집 구조

`menu_idx=173&searchCate1=18` 평생학습강좌신청 메뉴는 현재 등록된 강좌가 없다. 같은 도서관의 teach 신청 링크를 탐색하여 실제 행이 있는 메뉴도 수집한다.

현재 수집되는 메뉴:

- `https://www.gbelib.kr/yd/module/teach/index.do?menu_idx=246&searchCate1=16`

목록은 `#list_mode .item` 카드에서 읽고, 각 카드의 `a.detail-btn` `keyvalue*` 값을 사용해 상세를 호출한다.

상세 URL 형식:

```text
/yd/module/teach/detail.do?group_idx={group_idx}&category_idx={category_idx}&teach_idx={teach_idx}&large_category_idx={large_category_idx}&menu_idx={menu_idx}&searchCate1={searchCate1}
```

## 구현 내용

- `Crawler/Crawler_MunicipalYaml.py`에 `collect_gbelib_library_teach` 추가.
- 같은 도서관 prefix의 `/module/teach/index.do` 링크를 탐색한다.
- 목록 카드에서 title, apply period, schedule, venue, target, capacity, status를 수집한다.
- 상세 페이지에서 period, schedule, target, venue, description, material note를 보강한다.
- 강의실이 지점으로 승격되지 않도록 `preserve_branch=True`를 사용한다.
- 지점명은 `경상북도교육청 영덕도서관`, 주소는 footer 원문 주소를 사용한다.

## 품질 결과

2026-06-05 개발 DB 저장 기준:

| 항목 | 결과 |
| --- | --- |
| 수집 건수 | 2 |
| DB 저장 | 2 |
| title | 2/2 |
| branch | 2/2 |
| address | 2/2 |
| period | 2/2 |
| schedule_raw | 2/2 |
| target | 2/2 |
| description | 2/2 |
| fee | 0/2 |

수강료는 사이트 목록/상세에 별도 필드가 없어 `0`으로 저장된다.

Latest report:

- `logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_040931.yaml`

## 실행 명령

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_WWW_GBELIB_KR_04DB1B82.py --per-target-limit 0 --max-pages 5 --detail-limit 20 --timeout 25 --save-db --mark-stale
python -X utf8 tools\maintenance\kakao_geocode_branches.py --provider MUNI_WWW_GBELIB_KR_04DB1B82 --timeout 20 --delay 0.1 --min-confidence 50
```
