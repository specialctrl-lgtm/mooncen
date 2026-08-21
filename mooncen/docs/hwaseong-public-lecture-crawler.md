# 화성특례시 통합예약 강좌 크롤러

## 대상
- Provider: `MUNI_YEYAK_HSCITY_GO_KR_2DFD650A`
- URL: `https://yeyak.hscity.go.kr/1002/3001/lectureAllList.do`
- 분류: 공공예약
- 운영 주체: 화성특례시 통합예약시스템

## 수집 방식
- 만세구·효행구·병점구·동탄구와 `ready`, `apply`, `wait`, `finish` 상태를 교차 순회한다.
- 같은 상태의 전체 지역 목록과 4개 구 합집합을 비교해 누락과 중복을 검출한다.
- 목록의 `li.table-list-item` 카드에서 강좌 ID, 제목, 운영기관, 강좌기간, 접수기간, 신청/대기 인원, 수강료, 문의처를 수집한다.
- 상세 URL은 `lectureDetail.do?lectureIdx=...` 형태로 구성한다.
- 상세의 `.detail-info` 정의 목록에서 운영기관, 접수방법, 강좌분류, 교육대상, 접수일시, 수강기간, 요일/시간, 장소, 수강료, 재료비, 강사명, 문의처를 보강한다.
- 상세의 `.detail-tab.info-tab` 본문과 이미지를 `description`, `image_url`로 저장한다.

## 도서관 지점 대조
- 공식 도서관 목록 `https://www.hscitylib.or.kr/intro/menu/10002/contents/40024/contents.do`에서 21개 공공도서관과 작은도서관 통합 메뉴를 확인한다.
- 각 지점의 `신청/참여 > 강좌신청` 목록 22개를 직접 순회한다.
- 지점 목록의 `lectureIdx`를 통합예약 전체 목록과 대조한다. 한 건이라도 통합예약 목록에서 빠지거나 지점 목록 구조가 바뀌면 완전 수집으로 처리하지 않는다.
- 중복 저장을 막기 위해 도서관 전용 별칭 Provider는 실행하지 않고 이 Provider가 소유한다.

## 품질
2026-07-28 실시간 전체 저장 기준:

- 통합예약 현재 강좌: 994건
- 도서관 지점 목록 현재 강좌: 186건
- 공식 도서관 경로: 22개, 오류 0개
- 강좌가 있는 도서관 경로: 21개
- 도서관 186건의 대상·요금·날짜·장소·분야·시간: 186/186
- 구조화된 정확한 시작 시각: 185/186

## 실행 명령
```powershell
python -X utf8 Crawler\generated_yaml\MUNI_YEYAK_HSCITY_GO_KR_2DFD650A.py --per-target-limit 0 --max-pages 80 --detail-limit 1200 --timeout 30 --save-db --mark-stale
```

```powershell
python -X utf8 run_crawlers.py --providers MUNI_YEYAK_HSCITY_GO_KR_2DFD650A --once --ignore-active-window
```
