# 상주시 통합예약 교육강좌 크롤러

## 대상

- Provider: `MUNI_WWW_SANGJU_GO_KR_A813366C`
- 이름: 상주시 통합예약 교육강좌
- 목록 URL: `https://www.sangju.go.kr/page/15375/11881.tc?pageIndex=1`
- 상세 URL 형식: `https://www.sangju.go.kr/reserve/reservation/detail.tc?mn=15375&pageNo=11881&searchTrgtClsfCd=RMS004001&searchFcltNo=&cyclNo={cyclNo}`

## 구현

- 파일: `Crawler/generated_yaml/MUNI_WWW_SANGJU_GO_KR_A813366C.py`
- 등록: `run_crawlers.py`
- 레지스트리: `config/generated_yaml_crawler_registry.yaml`
- 대상 YAML: `config/crawl_targets/lifelong_learning.yaml`

목록 페이지의 `#reserveList section` 카드에서 강좌명, 지점, 주소, 기간, 상태, 접수 정보를 수집한다. `reserveList.detail('{cyclNo}')`에서 상세 번호를 추출한 뒤 상세 페이지를 요청해서 설명, 이미지, 추가 표 필드를 보강한다.

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

교육기간 종료 강좌는 기본적으로 저장하지 않는다. 필요할 때만 `--include-expired` 옵션으로 포함한다.

## 실행

```bash
python -X utf8 Crawler/generated_yaml/MUNI_WWW_SANGJU_GO_KR_A813366C.py --save-db
python -X utf8 run_crawlers.py --providers MUNI_WWW_SANGJU_GO_KR_A813366C --once --ignore-active-window
```

## 검증 결과

- 샘플 실행: 10건
- 품질 점수: 91.0
- 등급: A
- DB 저장 테스트: 2/2 성공

필드 채움 현황은 다음과 같다.

| 필드 | 채움 |
| --- | ---: |
| title | 10/10 |
| branch | 10/10 |
| address | 10/10 |
| period | 10/10 |
| schedule_raw | 10/10 |
| target | 2/10 |
| fee | 9/10 |
| status | 10/10 |
| description | 10/10 |
| image_url | 10/10 |

`target`은 사이트 자체에 대상 정보가 없는 강좌가 있어 누락될 수 있다.
