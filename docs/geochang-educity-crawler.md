# 거창군평생학습센터 크롤러

## 대상
- Provider: `MUNI_EDUCITY_GEOCHANG_GO_KR_3187BF2A`
- 목록 URL: `https://educity.geochang.go.kr/E0003/30020201.asp`
- 상세 URL 패턴: `https://educity.geochang.go.kr/E0003/30020203.asp?lc={강좌번호}`

## 수집 방식
- 목록은 `.listover` 카드에서 강좌번호, 제목, 상태, 접수기간, 교육기간, 대상, 장소, 문의전화, 이미지를 수집한다.
- 상세는 목록의 `30020203.asp?lc=...` 링크를 목록 URL 기준으로 절대 URL 변환 후 요청한다.
- 상세의 `.sub0202_view_wrap .con01`에서 기관, 접수, 일정, 대상, 장소를 보강한다.
- 상세의 `table.basic_tbl01`에서 강사명, 수강료, 교육정원, 접수방법, 개인준비물을 보강한다.

## 지점 처리
- 상세 `기관` 값을 branch로 저장한다.
- 문의전화는 `기관` 또는 목록의 `문의` 값을 사용한다.
- 상세 장소는 외부 답사지나 시설명인 경우가 많아 `venue_name`/`room`에 저장한다.
- 지도용 branch 주소는 기본값 `경상남도 거창군 거창읍 중앙로 103`을 사용한다.

## 종료 강좌 처리
- 기본 실행에서는 교육기간 종료일이 오늘보다 과거인 강좌를 저장하지 않는다.
- 과거 강좌 확인이 필요하면 `--include-expired`를 사용한다.

## 검증
- 2026-06-08 샘플 10건 기준 품질 점수: 94.0
- 채움률: title/branch/address/period/schedule_raw/target/fee/status/image_url 10/10
- description은 상세 학습목표/계획이 비어 있거나 `.`인 강좌가 있어 4/10이다.

## 실행
```bash
python -X utf8 Crawler/generated_yaml/MUNI_EDUCITY_GEOCHANG_GO_KR_3187BF2A.py --limit 10
python -X utf8 Crawler/generated_yaml/MUNI_EDUCITY_GEOCHANG_GO_KR_3187BF2A.py --save-db
python -X utf8 run_crawlers.py --providers MUNI_EDUCITY_GEOCHANG_GO_KR_3187BF2A --once --ignore-active-window
```
