# 공주시 평생학습포털 크롤러

## 대상
- Provider: `MUNI_WWW_GONGJU_GO_KR_7CBA2D38`
- 목록 URL: `https://www.gongju.go.kr/prog/nurimLeaEducate/E01/nurim/sub03_01/list.do`
- 상세 URL 패턴: `https://www.gongju.go.kr/prog/leaEducate/E01/learning/sub02_02/view.do?eduNo={강좌번호}`

## 수집 방식
- 목록은 `.courses_wrap .list` 카드에서 강좌번호, 제목, 상태, 접수기간, 교육기간, 교육시간, 신청/정원, 교육기관, 교육장소를 수집한다.
- 상세는 목록 카드의 `view.do?eduNo=...` 링크를 따라가고, 상세 `table tr`의 `th/td` 쌍을 파싱한다.
- 상세에서 강좌명, 강좌구분/분야, 접수기간, 교육기간, 교육시간, 교육대상, 교육장소, 정원, 강사명, 문의전화, 교육기관, 수강료, 교육내용, 강사소개를 보강한다.

## 지점 처리
- 지도 표시를 위해 `교육장소`를 우선 branch로 사용한다.
- `교육장소`가 `시설명(주소)` 형태면 시설명은 branch, 괄호 안 주소는 address로 저장한다.
- 괄호 안 값이 `2층`처럼 주소가 아닌 경우에는 주소로 쓰지 않고 기본 주소 `충청남도 공주시 봉황로 1`을 사용한다.

## 종료 강좌 처리
- 기본 실행에서는 교육기간 종료일이 오늘보다 과거인 강좌를 저장하지 않는다.
- 파서 품질 확인이 필요하면 `--include-expired`로 종료 강좌까지 포함해 테스트한다.

## 검증
- 2026-06-09 기본 실행 기준 활성 강좌 2건 수집, 품질 점수: 90.0
- 채움률: title/branch/address/period/schedule_raw/target/fee/status/description 2/2
- `image_url`은 목록 HTML 주석 안에만 이미지가 있어 일반 수집 필드에서는 0/2이다.
- `run_crawlers.py` 경유 DB 저장 테스트: 2/2 성공

## 실행
```bash
python -X utf8 Crawler/generated_yaml/MUNI_WWW_GONGJU_GO_KR_7CBA2D38.py --save-db
python -X utf8 Crawler/generated_yaml/MUNI_WWW_GONGJU_GO_KR_7CBA2D38.py --limit 10 --include-expired
python -X utf8 run_crawlers.py --providers MUNI_WWW_GONGJU_GO_KR_7CBA2D38 --once --ignore-active-window
```
