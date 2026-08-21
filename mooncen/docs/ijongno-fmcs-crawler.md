# 종로구시설관리공단 FMCS 크롤러

## 대상

- Provider: `MUNI_WWW_IJONGNO_CO_KR_F9ED1CA5`
- URL: `https://www.ijongno.co.kr/fmcs/3`
- 분류: 체육/스포츠, 지자체/공공기관
- 파서: `fmcs_lecture_api`

## 수집 구조

종로구시설관리공단 FMCS 목록은 화면에서 `전체`를 선택하고 검색해야 목록이 채워진다. 실제 수집은 우선 UI의 `전체 + 검색`과 같은 API 호출을 사용한다.

- 전체 검색: `POST /rest/lecture/list`
  - `company_code=""`
  - `search_type="%"`
  - `category_cd=""`
  - `category_level="9"`

전체 검색 결과가 비어 있으면 다음 API를 순환하는 fallback을 사용한다.

- 센터: `POST /rest/common/company` with `type=L`
- 종목/강좌: `POST /rest/common/category` with `company_code`, 하위 강좌는 `code=<상위 category_code>`
- 강좌 목록: `POST /rest/lecture/list`

목록 조회는 사이트 JS와 동일하게 `search_type=%`를 사용한다. 응답의 `status` 값을 그대로 상태로 변환한다.

## 구현 내용

- `Crawler/Crawler_MunicipalYaml.py`의 FMCS 공통 파서에 category context 탐색을 추가했다.
- 종로 provider는 전체 검색을 우선 사용한다.
- fallback 수집 조합은 `센터 x 상위 종목 x 하위 강좌 x page`이며, 빈 category fallback도 포함한다.
- 상위/하위 category에서 같은 강좌가 반복될 수 있으므로 `provider_course_id` 기준으로 dedupe한다.
- `max_pages`는 전체 API 호출 제한이 아니라 각 조합별 페이지 제한으로 적용한다.
- 지점은 API의 `comcd/comnm` 기준으로 분리한다.

## 품질 결과

2026-06-05 개발 DB 저장 기준:

| 항목 | 결과 |
| --- | --- |
| 수집 건수 | 438 |
| DB 저장 | 438 |
| 지점 | 올림픽기념국민생활관 138, 종로구민회관 114, 종로문화체육센터 186 |
| 상태 | CLOSED 438 |
| title | 438/438 |
| branch | 438/438 |
| schedule_raw | 438/438 |
| fee | 438/438 |
| target | 438/438 |
| description | 73/438 |
| period | 0/438 |

Latest report:

- `logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_035932.yaml`
- API list pages: 5

## 한계

강좌 API와 상세 페이지에 정확한 시작일/종료일이 없다. 상세의 `수강기간`은 `1개월` 같은 상품 기간이므로 `period`로 저장하지 않는다.

## 주소/좌표

강좌 API는 지점 주소를 제공하지 않는다. 저장 후 Google Geocoding missing-only 백필로 지점 좌표를 채운다.

실행 결과:

- 서울특별시 종로구
- 올림픽기념국민생활관: 대한민국 서울특별시 종로구 성균관로 91
- 종로구민회관: 대한민국 서울특별시 종로구 지봉로5길 7-5
- 종로문화체육센터: 대한민국 서울특별시 종로구 인왕산로1길 21

## 실행 명령

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_WWW_IJONGNO_CO_KR_F9ED1CA5.py --per-target-limit 0 --max-pages 20 --detail-limit 80 --timeout 25 --save-db --mark-stale
python -X utf8 tools\maintenance\kakao_geocode_branches.py --provider MUNI_WWW_IJONGNO_CO_KR_F9ED1CA5 --timeout 20 --delay 0.1 --min-confidence 50
```
