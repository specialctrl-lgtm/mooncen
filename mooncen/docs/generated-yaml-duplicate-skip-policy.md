# Generated YAML Duplicate Skip Policy

## 목적

YAML 기반 기타/공공 크롤러에서 같은 URL 또는 같은 수집 범위를 가진 provider가 중복 실행되지 않도록 한다.

## 제외 기준

다음 조건 중 하나라도 해당하면 해당 크롤러는 실행 대상에서 제외한다.

- `duplicate_of`가 설정된 경우
- `collection_type: duplicate`인 경우
- `blocked_reason` 또는 `last_quality.error_kind`가 `duplicate_of:`로 시작하는 경우
- 정규화 URL이 이미 선택된 다른 provider와 같은 경우

## URL 정규화

중복 판정용 URL은 다음 값을 제거하고 비교한다.

- 페이지 번호: `page`, `pageIndex`, `currentPage`, `currentPageNo`, `pageNo`, `nowPage`
- 페이지 크기/뷰 옵션: `pageUnit`, `pageSize`, `recordCountPerPage`, `rows`, `viewType`
- 임시 값: `token`, `timestamp`, `ts`, `_`, `callback`

기관, 카테고리, 메뉴를 의미하는 파라미터는 유지한다. 예를 들어 `key`, `menuCd`, `srchResveInsttCd`, `searchCate1` 등은 수집 범위를 바꿀 수 있으므로 중복 제거하지 않는다.

## 선택 우선순위

같은 수집 범위가 여러 개면 아래 기준으로 하나만 남긴다.

1. 낮은 `priority`
2. `crawler_status` 우선순위: `ready`, `partial`, `candidate`, `needs_parser`, `needs_discovery`, `blocked`
3. `source`
4. `provider`

## 적용 위치

- 실행 대상 로딩: `Crawler/Crawler_GeneratedYamlTargets.py`
- registry 생성: `config/generated_yaml_crawler_registry.yaml`

registry에서는 중복 provider를 삭제하지 않고 `enabled: false`, `disabled_reason: duplicate_url:{대표 provider}` 또는 `duplicate_of:{대표 provider}`로 표시한다.
