# MoonCen Crawl Targets

이 폴더는 수집 대상을 사용자 카테고리와 운영 상태 기준으로 분리한 레지스트리입니다.

## Field Guide

| field | meaning |
| --- | --- |
| `collection_category` | DB와 API에서 사용하는 수집 카테고리입니다. `domain_category`와 같은 값으로 저장합니다. |
| `domain_category` | 사용자 화면에 노출하기 쉬운 분류입니다. |
| `operator_type` | 운영 주체입니다. 지자체/공공기관, 복지기관, 대형마트/백화점 등입니다. |
| `source_group` | 크롤러 스케줄과 운영 묶음입니다. |
| `service_group` | 사용자/운영 상위 분류입니다. `체험`, `공공강좌`, `문화센터`, `기타` 중 하나입니다. |
| `collection_type` | 수집 방식입니다. `static_html`, `ajax_api`, `selenium`, `document`, `unknown` 등이 있습니다. |
| `crawler_status` | 운영 상태입니다. `ready`, `partial`, `needs_parser`, `needs_discovery`, `blocked`, `candidate`, `deprecated`입니다. |
| `last_quality` | 최근 10건 샘플 품질 리포트에서 가져온 결과입니다. |

## Status Flow

`candidate -> needs_discovery/needs_parser -> partial -> ready`

`ready`인 대상만 자동수집 스케줄에 넣는 것을 기본 원칙으로 둡니다.
