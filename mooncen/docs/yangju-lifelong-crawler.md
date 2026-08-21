# 양주시평생학습센터 크롤러

## 대상

- Provider: `MUNI_WWW_YANGJU_GO_KR_1A2AECAC`
- 이름: 양주시평생학습센터
- 목록 URL: `https://www.yangju.go.kr/lll/selectEduLctreWebList.do?key=1838&lllKind=1`
- 상세 URL 형식: `https://www.yangju.go.kr/lll/eduLctreWebView.do?key=1838&eduLctreNo={eduLctreNo}`

## 구현

- 파일: `Crawler/generated_yaml/MUNI_WWW_YANGJU_GO_KR_1A2AECAC.py`
- 레지스트리: `config/generated_yaml_crawler_registry.yaml`
- 대상 YAML: `config/crawl_targets/lifelong_learning.yaml`

목록의 `table.list_table` 행에서 강좌명, 접수기간, 교육기간, 교육일시, 정원/대기, 수강료, 접수상태를 수집한다. 상세 페이지의 `.education_request li.clearfix`에서 교육기관, 교육장소, 강의실, 분류, 수강대상, 강사명, 전화번호, 강의개요, 유의사항을 보강한다.

## 지점 처리

상세의 `교육장소`가 실제 지점 역할을 하므로 branch로 사용한다. `교육장소`가 `기타`, `온라인`, 공백이면 `교육기관`을 branch로 사용한다.

주요 평생학습센터는 주소 보정 맵으로 지도 표시를 보강한다.

| 지점 | 주소 |
| --- | --- |
| 양주시평생학습관 | 경기도 양주시 부흥로 1533 |
| 옥정평생학습센터 | 경기도 양주시 옥정동로7길 110 |
| 옥정서부평생학습센터 | 경기도 양주시 옥정서로 42 |
| 덕계평생학습관 | 경기도 양주시 평화로1475번길 39 |
| 백석평생학습관 | 경기도 양주시 백석읍 중앙로223번길 46 |
| 율정평생학습센터 | 경기도 양주시 옥정로 397-7 |
| 광적평생학습센터 | 경기도 양주시 광적면 가래비길 93 |
| 은현평생학습센터 | 경기도 양주시 은현면 은현로 66 |
| 덕정평생학습센터 | 경기도 양주시 화합로 1426 |

## 수집 필드

- `title`
- `branch`
- `address`
- `period`
- `schedule_raw`
- `target`
- `fee`
- `status`
- `description`
- `raw_url`

상세 페이지에 강좌 이미지가 없어 `image_url`은 비어 있을 수 있다. 교육기간이 종료된 강좌는 기본 실행에서 저장하지 않는다.

## 실행

```bash
python -X utf8 Crawler/generated_yaml/MUNI_WWW_YANGJU_GO_KR_1A2AECAC.py --save-db
python -X utf8 run_crawlers.py --providers MUNI_WWW_YANGJU_GO_KR_1A2AECAC --once --ignore-active-window
```

품질 평가용 과거 강좌 포함 실행:

```bash
python -X utf8 Crawler/generated_yaml/MUNI_WWW_YANGJU_GO_KR_1A2AECAC.py --limit 10 --include-expired
```

## 검증 결과

- 샘플 실행: 10건
- 품질 점수: 90.0
- 등급: A
- 기본 실행 저장 대상: 0건

현재 목록의 샘플 강좌는 2025년 교육기간 종료 강좌라 기본 저장 대상에서 제외된다.

| 필드 | 채움 |
| --- | ---: |
| title | 10/10 |
| branch | 10/10 |
| address | 10/10 |
| period | 10/10 |
| schedule_raw | 10/10 |
| target | 10/10 |
| fee | 10/10 |
| status | 10/10 |
| description | 10/10 |
| image_url | 0/10 |
