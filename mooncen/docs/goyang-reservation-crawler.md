# 고양시 통합예약 교육강좌 크롤러

## 대상

- Provider: `MUNI_WWW_GOYANG_GO_KR_AFE8FBDD`
- 이름: 고양시 통합예약 교육강좌
- 목록 URL: `https://www.goyang.go.kr/resve/manage/BD_selectResveManageList.do?q_resveTopClCode=CL_01`
- 상세 URL 형식: `https://www.goyang.go.kr/resve/manage/BD_selectResveManage.do?q_resveTopClCode=CL_01&resveSn={resveSn}`

## 구현

- 파일: `Crawler/generated_yaml/MUNI_WWW_GOYANG_GO_KR_AFE8FBDD.py`
- 레지스트리: `config/generated_yaml_crawler_registry.yaml`
- 대상 YAML: `config/crawl_targets/lifelong_learning.yaml`

목록의 `opResveView(resveSn, ...)` 값을 상세 식별자로 사용한다. 목록에서 제목, 장소, 대상, 신청기간, 교육기간, 정원, 수강료, 접수상태를 우선 수집하고 상세 페이지에서 설명, 이미지, 담당자, 신청방법, 장소 정보를 보강한다.

## 지점 처리

통합예약은 강좌별 장소가 지점 역할을 하므로 장소명 기준으로 branch를 분리한다. 상세 주소가 없는 주요 장소는 다음 보정 맵을 사용한다.

| 장소 | 주소 |
| --- | --- |
| 고양시청 | 경기도 고양시 덕양구 고양시청로 10 |
| 덕양구청 | 경기도 고양시 덕양구 화중로104번길 13 |
| 일산동구청 | 경기도 고양시 일산동구 중앙로 1256 |
| 일산서구청 | 경기도 고양시 일산서구 중앙로 1600 |
| 고양종합운동장 | 경기도 고양시 일산서구 중앙로 1601 |

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
- `image_url`
- `raw_url`

교육기간이 종료된 강좌는 기본적으로 저장하지 않는다. 품질 평가나 과거 데이터 확인이 필요할 때만 `--include-expired` 옵션을 사용한다.

## 실행

```bash
python -X utf8 Crawler/generated_yaml/MUNI_WWW_GOYANG_GO_KR_AFE8FBDD.py --save-db
python -X utf8 run_crawlers.py --providers MUNI_WWW_GOYANG_GO_KR_AFE8FBDD --once --ignore-active-window
```

## 검증 결과

- 샘플 실행: 10건
- 품질 점수: 100.0
- 등급: A
- DB 저장 테스트: 2/2 성공

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
| image_url | 10/10 |
