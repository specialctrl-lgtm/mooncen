# 성남 배움숲 분당구청 시민정보화교육 크롤러

## Provider
- `MUNI_SUGANG_SEONGNAM_GO_KR_D447262D`

## Source URL
- `https://sugang.seongnam.go.kr/ilms/learning/learningList.do?searchUseYn=Y&searchCondition3=OFFICE_00000670`

## Parser
- 기존 `SEONGNAM_BAEUMSOOP` 전용 파서를 재사용한다.
- 이 provider는 `OFFICE_00000670` 단일 기관만 수집한다.
- 지점명은 `분당구청 시민정보화교육`으로 고정한다.
- 주소는 `경기도 성남시 분당구 분당로 50`으로 고정한다.
- 상세 페이지는 `fn_learning_detail(learning_id)`에서 `learning_id`를 추출해 `learningDetail.do`로 조회한다.
- `접수연습용`, `실제강의 아님`, 교육기간 종료 강좌는 수집에서 제외한다.

## Quality
- 샘플 수집 결과: 2건
- 저장 검증: 2건 저장 성공
- 필드 채움:
  - title 2/2
  - branch 2/2
  - address 2/2
  - period 2/2
  - schedule_raw 2/2
  - target 2/2
  - fee 2/2
  - status 2/2
  - description 2/2
  - image_url 0/2
  - application_url 0/2
- 현재 노출 강좌는 `교육중` 상태라 신청 URL은 비워진다.

## Commands
```bash
python -X utf8 Crawler/generated_yaml/MUNI_SUGANG_SEONGNAM_GO_KR_D447262D.py --limit 10
python -X utf8 Crawler/generated_yaml/MUNI_SUGANG_SEONGNAM_GO_KR_D447262D.py --limit 10 --save-db
python -X utf8 run_crawlers.py --providers MUNI_SUGANG_SEONGNAM_GO_KR_D447262D --limit 10 --once --ignore-active-window --ignore-worker-lock --skip-coordinate-backfill --skip-category-backfill
```
