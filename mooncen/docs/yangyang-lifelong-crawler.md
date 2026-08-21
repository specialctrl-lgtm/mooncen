# 양양군평생학습관 크롤러

## 대상

- Provider: `MUNI_EDU_YANGYANG_GO_KR_06A9551C`
- 이름: 양양군평생학습관
- 목록 URL: `https://edu.yangyang.go.kr/lecture/class_list.php`
- 특별강좌 URL: `https://edu.yangyang.go.kr/lecture/class_list.php?lco_type=1`

## 구현

- 파일: `Crawler/generated_yaml/MUNI_EDU_YANGYANG_GO_KR_06A9551C.py`
- 레지스트리: `config/generated_yaml_crawler_registry.yaml`
- 대상 YAML: `config/crawl_targets/lifelong_learning.yaml`

목록 페이지의 `.req_list` 카드에서 강좌명, 접수상태, 강의기간, 강의시간, 수강료, 강의장소, 정원/신청인원, 접수기간을 수집한다. 별도 상세 페이지가 없고 `더보기` 영역에 강의내용과 이미지가 포함되어 있어 collapse 영역까지 함께 파싱한다.

## 수집 필드

- `title`
- `branch`
- `address`
- `period`
- `schedule_raw`
- `target`
- `fee`
- `status`
- `image_url`
- `raw_url`

강의내용은 대부분 텍스트 없이 포스터 이미지로 구성되어 있어 `description`은 비어 있을 수 있다. 교육기간이 종료된 강좌는 기본적으로 저장하지 않으며, 필요할 때만 `--include-expired` 옵션으로 포함한다.

## 실행

```bash
python -X utf8 Crawler/generated_yaml/MUNI_EDU_YANGYANG_GO_KR_06A9551C.py --save-db
python -X utf8 run_crawlers.py --providers MUNI_EDU_YANGYANG_GO_KR_06A9551C --once --ignore-active-window
```

## 검증 결과

- 샘플 실행: 10건
- 품질 점수: 90.0
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
| description | 0/10 |
| image_url | 10/10 |
