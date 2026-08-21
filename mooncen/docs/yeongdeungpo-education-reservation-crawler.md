# 영등포구 통합예약 교육강좌 크롤러

## 대상
- Provider: `MUNI_WWW_YDP_GO_KR_02AFDA7A`
- URL: `https://www.ydp.go.kr/reserve/selectTnEdcLctreListU.do?key=5062&`
- 분류: 공공예약
- 운영 주체: 영등포구 통합예약서비스

## 수집 방식
- 목록의 `li.typ1` 카드에서 상태, 제목, 교육장소, 대상, 접수기간, 교육기간, 접수방법, 정원 정보를 수집한다.
- 페이지 이동은 `cpn` GET 파라미터로 처리한다.
- 상세 URL은 `viewTnEdcLctreU.do?lctreNo=...&user=USER&key=5062` 형태를 사용한다.
- 상세 `table.p-table.block`에서 과정명, 교육장소, 강사명, 접수방식, 수강대상, 수강료, 재료비, 접수기간, 교육기간, 강의요일, 정원, 강의개요, 수강신청유의사항, 교육과정문의, 관심분야, 강의계획서를 보강한다.
- 교육기간 종료일이 지난 강좌는 기본 수집에서 제외한다.

## 지점/주소 처리
- 상세 교육장소 또는 제목 괄호 안의 주민센터/학습관명을 지점명으로 사용한다.
- 주요 주민센터와 `YDP미래평생학습관`은 정적 주소 매핑을 사용한다.
- 매핑이 없는 경우 영등포구청 기본 주소를 fallback으로 사용한다.

## 품질
- 샘플 수집: 10건
- DB 저장: 10/10 성공
- 품질 점수: 90.0
- 누락 필드: `image_url`
- 비고: 상세 페이지에 강좌 대표 이미지가 없어 이미지 누락은 정상 한계로 처리한다.

## 실행 명령
```bash
python -X utf8 Crawler/generated_yaml/MUNI_WWW_YDP_GO_KR_02AFDA7A.py --save-db
```

```bash
python -X utf8 run_crawlers.py --providers MUNI_WWW_YDP_GO_KR_02AFDA7A --once --ignore-active-window
```
