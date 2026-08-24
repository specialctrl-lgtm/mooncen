# 유성구 평생학습센터 크롤러

## 대상
- Provider: `MUNI_LIFELONG_YUSEONG_GO_KR_E36DECD2`
- 목록 URL: `https://lifelong.yuseong.go.kr/lly/prog/lctr/lly/sub02_01/SEARCH/classList.do`
- 상세 URL: `https://lifelong.yuseong.go.kr/lly/prog/lctr/lly/sub02_01/SEARCH/classDetail.do`

## 수집 방식
- 목록은 `a.inner-box.button_view[data-key-no]` 카드에서 강좌번호, 제목, 상태, 기간, 시간, 대상, 수강료를 수집한다.
- 상세는 `classDetail.do`에 `lctrNo`를 포함해 POST 요청한다.
- 상세의 `.subjact/.con` 쌍에서 교육기간, 교육시간, 문의처, 교육장소, 수강료, 교육대상, 강사명, 준비물을 보강한다.
- 상세의 `.detail-index` 섹션에서 `강좌내용`, `유의사항`을 `description`으로 합친다.

## 지점 처리
- 상세에 포함된 `구암센터`, `전민센터` 주소/전화 정보를 파싱한다.
- 제목, 목록 배지, 문의처, 교육장소 텍스트에 `전민`이 있으면 `전민센터`, `구암`이 있으면 `구암센터`로 저장한다.
- 판단이 어려운 경우 기본 지점은 `유성구 평생학습센터`로 둔다.
- DB 저장 시 `branches.address`, `branches.phone`, `branches.website_url`, `branches.address_source`를 함께 업데이트한다.

## 종료 강좌 처리
- 기본 실행에서는 교육기간 종료일이 오늘보다 과거인 강좌를 저장하지 않는다.
- 과거 강좌까지 확인할 때는 `--include-expired`를 사용한다.

## 검증
- 2026-06-08 샘플 10건 기준 품질 점수: 90.0
- 채움률: title/branch/address/period/schedule_raw/target/fee/status/description 10/10
- `image_url`은 사이트 상세에 대표 이미지가 없어 0/10이다.

## 실행
```bash
python -X utf8 Crawler/generated_yaml/MUNI_LIFELONG_YUSEONG_GO_KR_E36DECD2.py --limit 10
python -X utf8 Crawler/generated_yaml/MUNI_LIFELONG_YUSEONG_GO_KR_E36DECD2.py --save-db
python -X utf8 run_crawlers.py --providers MUNI_LIFELONG_YUSEONG_GO_KR_E36DECD2 --once --ignore-active-window
```
