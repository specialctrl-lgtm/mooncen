# 순천시평생교육포털 통합강좌 크롤러

## 대상
- Provider: `MUNI_LMS_SCHC_GO_KR_A117B76B`
- URL: `https://lms.schc.go.kr/lms/class_01.do`
- 분류: 평생학습
- 운영 주체: 순천시평생교육포털

## 수집 방식
- 목록 페이지의 강좌 테이블을 파싱한다.
- 페이지 이동은 `nowPage` POST 파라미터로 처리한다.
- 상세 페이지는 `mode=view&iEduLgrpCd=...&iClassIdx=...` 쿼리 URL로 접근한다.
- 상세의 `기본정보`, `상세정보`, `교육내용` 테이블에서 기간, 대상, 수강료, 기관, 장소, 문의전화, 교육일정, 설명을 보강한다.
- 교육기간 종료일이 지난 강좌는 기본 수집에서 제외한다.

## 지점/주소 처리
- 상세 교육내용이나 교육장소에서 `순천시 ...로/길 번호` 패턴을 우선 추출한다.
- 주소가 없는 경우 주요 기관 매핑을 사용한다.
- 매핑에도 없으면 `전라남도 순천시`를 기본 주소로 저장한다.

## 품질
- 샘플 수집: 10건
- DB 저장: 10/10 성공
- 품질 점수: 90.0
- 누락 필드: `image_url`
- 비고: 목록/상세에 강좌 대표 이미지가 없어 이미지 누락은 정상 한계로 처리한다.

## 실행 명령
```bash
python -X utf8 Crawler/generated_yaml/MUNI_LMS_SCHC_GO_KR_A117B76B.py --save-db
```

```bash
python -X utf8 run_crawlers.py --providers MUNI_LMS_SCHC_GO_KR_A117B76B --once --ignore-active-window
```
