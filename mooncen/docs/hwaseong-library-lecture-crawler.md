# 화성특례시 통합예약 도서관 강좌 크롤러

## 대상
- Provider: `MUNI_YEYAK_HSCITY_GO_KR_E7FCC3C0`
- URL: `https://yeyak.hscity.go.kr/1002/3001/lectureList.do?currentPageNo=1&recordCountPerPage=10&searchCondition=lectureNm&searchInstitutionTypeCd=INS01&searchAreaEmd=&statusCd=&freeYn=&targetCd=`
- 분류: 도서관
- 운영 주체: 화성특례시 통합예약시스템

## 수집 방식
- 목록의 `li.table-list-item` 카드에서 강좌 ID, 제목, 운영기관, 강좌기간, 접수기간, 신청/대기 인원, 수강료, 문의처를 수집한다.
- 상세 URL은 `lectureDetail.do?lectureIdx=...` 형태로 구성한다.
- 상세의 `.detail-info` 정의 목록에서 운영기관, 접수방법, 강좌분류, 교육대상, 접수일시, 수강기간, 요일/시간, 장소, 수강료, 재료비, 강사명, 문의처를 보강한다.
- 상세의 `.detail-tab.info-tab` 본문과 이미지를 `description`, `image_url`로 저장한다.
- 교육기간 종료일이 지난 강좌는 기본 수집에서 제외한다.

## 지점/주소 처리
- `운영기관`을 지점명으로 사용한다.
- 지점명에 붙는 `바로가기` 문구는 제거한다.
- 주요 도서관은 정적 주소 매핑을 사용한다.
- 매핑이 없는 지점은 화성시청 기본 주소를 fallback으로 사용한다.

## 품질
- 샘플 수집: 10건
- DB 저장: 10/10 성공
- 품질 점수: 99.0
- 누락 필드: `description` 1건. 해당 상세가 이미지 중심으로 구성된 경우이다.

## 실행 명령
```bash
python -X utf8 Crawler/generated_yaml/MUNI_YEYAK_HSCITY_GO_KR_E7FCC3C0.py --save-db
```

```bash
python -X utf8 run_crawlers.py --providers MUNI_YEYAK_HSCITY_GO_KR_E7FCC3C0 --once --ignore-active-window
```
