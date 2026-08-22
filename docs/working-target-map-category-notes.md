# Working Target Collection And Category Map

## 목적

지도에는 수집이 가능한 대상의 데이터만 노출하고, 강좌가 속한 수집 카테고리 기준으로 지점 마커와 필터를 분리한다.

## 수집 대상 기준

생성형 YAML 크롤러는 `config/crawl_targets/*.yaml`을 읽는다.

자동 실행 대상은 `crawler_status`가 다음 값인 경우만 포함한다.

| status | 의미 |
| --- | --- |
| `ready` | 현재 수집 가능 |
| `partial` | 일부 필드는 부족하지만 목록 수집 가능 |

다음 대상은 생성형 YAML 크롤러에서 제외한다.

| 제외 대상 | 이유 |
| --- | --- |
| 전용 크롤러가 있는 provider | HOMEPLUS, LOTTE, EMART 등은 전용 파서로 수집해야 품질이 안정적이다. |
| `blocked`, `needs_parser`, `needs_discovery`, `candidate`, `deprecated` | 현재 자동 수집 품질을 보장하기 어렵다. |
| `e-ncom.co.kr` URL | 제외 요청된 도메인이다. |

레지스트리 갱신:

```bash
python -X utf8 Crawler/Crawler_GeneratedYamlTargets.py --write-registry
```

샘플 수집:

```bash
python -X utf8 Crawler/Crawler_GeneratedYamlTargets.py --all --target-limit 10 --per-target-limit 10 --save-db
```

## 지도 카테고리 표시

`/api/branches/nearby` API는 지점별 활성 강좌 수와 함께 카테고리 집계를 내려준다.

| 필드 | 의미 |
| --- | --- |
| `collection_categories` | 해당 지점에 있는 활성 강좌의 수집 카테고리 목록 |
| `category_counts` | 카테고리별 활성 강좌 수 |
| `primary_collection_category` | 가장 많은 강좌가 속한 대표 카테고리 |

프론트 지도는 `primary_collection_category` 색상으로 마커를 표시하고, 정보창에 `category_counts`를 칩으로 보여준다.

## 검증 결과

2026-05-27 기준 생성형 YAML 자동 대상은 435개다. 샘플 10개 provider 저장 테스트는 모두 성공했다.

| provider | saved |
| --- | ---: |
| GWACHEON_NATIONAL_SCIENCE_MUSEUM | 2 |
| MARINE_BIODIVERSITY_INSTITUTE | 2 |
| NATIONAL_ECOLOGY_CENTER | 4 |
| NATIONAL_PARK_RESERVATION | 3 |
| DAEGU_RESERVATION | 10 |
| GWANGJU_RESERVATION | 2 |
| INCHEON_RESERVATION | 4 |
| HADONG_WELFARE_ACADEMY_COURSE | 10 |
| HAMAN_WELFARE_LIFELONG_COURSE | 2 |
| HAMYANG_WELFARE_OFFICIAL_COURSE | 10 |

## 2026-05-27 UI 보정

지도 마커와 결과 목록에는 provider 코드 대신 사용자가 읽을 수 있는 기관명을 표시한다. 기관명을 확인할 수 없으면 원시 코드를 그대로 노출하지 않고 `교육·체험` 같은 기본 서비스명을 표시한다.

Provider 표시명은 가능한 경우 `config/crawl_targets/*.yaml`의 `name`과 API의 `provider_label`을 사용한다. 같은 provider가 화면마다 다른 이름으로 보이지 않도록 공통 표시 규칙을 적용한다.

## 2026-05-28 Results List Layout

- 결과 목록의 지점별 강좌 카드는 가로 스크롤 대신 세로 스크롤로 표시한다.
- 지점 그룹 헤더는 문화센터명, 지점명, 결과 수를 각각 한 줄로 분리해서 긴 이름이 한 줄에 몰리지 않게 한다.
- 지점 그룹 안의 강좌 카드는 한 줄 높이만 보이게 하고, 나머지는 같은 영역 안에서 세로 스크롤로 확인한다.
- 지점 그룹 헤더는 목록 상단의 슬림한 한 줄 바 형태로 표시하며, 문화센터명/지점명/결과 수를 한 행에 배치한다.
- 축소 상태에서는 강좌 그리드를 1컬럼으로 고정해 가로 스크롤이 아니라 세로 스크롤만 사용한다.
