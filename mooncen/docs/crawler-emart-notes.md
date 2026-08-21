# EMART Crawler Notes

## 2026-05-28 GraphQL Pagination Fix

이마트 문화센터 목록 페이지는 React 화면에 최초 20개만 렌더링한다. 더보기 버튼이나 서버 렌더링 페이지네이션이 아니라 AppSync GraphQL `getClassByFiltering`에 `from`, `size`를 넘기는 무한스크롤 구조다.

`Crawler/Crawler_Emart.py`는 Selenium DOM 목록 수집을 fallback으로 남기고, 기본 경로를 GraphQL 수집으로 변경했다.

- 지점은 기존 Selenium 체크박스 목록에서 수집한다.
- 강좌는 `mainStoreInfo.storeCode`와 현재 학기 필터로 GraphQL 조회한다.
- 현재 학기는 `https://d24y2yfxh2iebm.cloudfront.net/public/default/defaultSemester.json`에서 읽는다.
- `EMART_GRAPHQL_PAGE_SIZE` 기본값은 `500`이다.
- `EMART_GRAPHQL_MAX_PAGES` 기본값은 `30`이다.
- GraphQL 실패 시 기존 Selenium DOM 수집으로 fallback한다.

검증 결과:

```bash
python -X utf8 Crawler/Crawler_Emart.py --limit 30
```

가든5점(`974`)에서 GraphQL `rows=30 total=336`으로 조회 및 저장되는 것을 확인했다. 기존 DOM 방식의 지점별 20건 제한은 GraphQL 경로에서는 발생하지 않는다.

## 2026-06-14 지도 미노출 원인 점검

메인 지도에서 이마트가 보이지 않는 원인을 점검했다.

- `/api/branches/providers` 기준 EMART 데이터는 존재한다.
- 기준점 수원 인근 30km 내 이마트 지점도 좌표가 존재한다.
- 다만 수원(TR), 광교, 흥덕, 동탄 등 인근 지점은 DB상 `active_course_count=0`이었다.
- 해당 지점들의 기존 DB 강좌는 대부분 `2026-05-16~2026-05-19` 기간의 `CLOSED` 강좌 20건이었다.
- 프론트는 기본적으로 마감 제외/활성 강좌 기준으로 지점을 보여주므로 active 0 지점은 지도와 지점 목록에서 빠진다.
- 직접 GraphQL 호출은 수원(TR), 광교, 흥덕, 동탄 지점 모두 2026년 6월 접수중 강좌를 정상 반환했다.

결론:

- 사이트/API 문제는 아니고, 이마트 최신 GraphQL 수집 결과가 전체 지점에 다시 반영되지 않아 DB가 오래된 상태였다.
- 과거 Selenium DOM fallback 수집 결과인 지점별 20건 데이터가 주변 지점에 남아 있었다.

코드 보강:

- GraphQL 응답에서 `mainCategory`, `subCategory`, `classDetail`, `mainImage`, `categoryImage`, `classTime`, `classDateInfo`, `materialCalculate`가 dict/string/null로 섞여도 파싱이 중단되지 않게 방어했다.
- 이마트 지점 체크박스 목록 Selenium 수집이 실패하면 DB에 이미 저장된 EMART 지점 목록을 fallback으로 사용한다.

운영 조치:

```bash
python -X utf8 run_crawlers.py --providers EMART --once --ignore-active-window
```

지점 단위 복구가 필요하면 다음처럼 실행한다.

```bash
python -X utf8 Crawler/Crawler_Emart.py --branch-code 979
python -X utf8 Crawler/Crawler_Emart.py --branch-code 991
python -X utf8 Crawler/Crawler_Emart.py --branch-code 960
python -X utf8 Crawler/Crawler_Emart.py --branch-code 947
```
