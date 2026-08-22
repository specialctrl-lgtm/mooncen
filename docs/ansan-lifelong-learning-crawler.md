# 안산시평생학습관 크롤러

## Provider
- `MUNI_LLL_ANSAN_GO_KR_691646BE`

## 수집 범위
- 피움과정: `https://lll.ansan.go.kr/web/cop/regEduList.do`
- 다채움: `https://lll.ansan.go.kr/web/cop/mulEduList.do`
- 길거리학습관/아파트학습관: `https://lll.ansan.go.kr/web/cop/roadEduList.do`
- 특별교육: `https://lll.ansan.go.kr/web/cop/norEduList.do`

## 구현 방식
- 목록 페이지의 `.list-board .board_section` 카드에서 강좌 ID, 제목, 상태, 교육기간, 요일, 시간, 강사명, 신청/정원을 추출한다.
- `fn_go_detail()`에 포함된 강좌 ID로 상세 페이지를 요청해 교육대상, 강의장, 수강료, 재료비, 설명을 보강한다.
- 길거리학습관/아파트학습관 과정은 상세 강의장을 지점명으로 사용한다.
- 교육기간 종료일이 지난 강좌는 수집 결과와 DB 저장 대상에서 제외한다.

## 검증 결과
- 명령: `python -X utf8 Crawler/generated_yaml/MUNI_LLL_ANSAN_GO_KR_691646BE.py --limit 10 --json`
- 수집: 10건
- 품질: A / 100.0
- 필드: title, branch, address, period, schedule_raw, target, fee, status, description, raw_url, instructor 모두 10/10

## 실행
```bash
python -X utf8 Crawler/generated_yaml/MUNI_LLL_ANSAN_GO_KR_691646BE.py --save-db
python run_crawlers.py --providers MUNI_LLL_ANSAN_GO_KR_691646BE --once --ignore-active-window --ignore-worker-lock
```
