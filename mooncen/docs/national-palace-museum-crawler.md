# NATIONAL_PALACE_MUSEUM crawler

## 목적

국립고궁박물관 교육신청 데이터를 수집한다.

기존 타겟 URL이 `https://www.gogung.go.kr` 루트로 되어 있어 generic parser가 대관료, 시설안내 같은 비강좌 콘텐츠를 강좌로 오수집했다. 실제 수집 URL은 교육신청 목록이다.

## 수집 URL

- 목록: `https://www.gogung.go.kr/gogung/pgm/cultureEventReg/edu/list.do?menuNo=800212`
- 상세: `https://www.gogung.go.kr/gogung/pgm/cultureEventReg/edu/updtView.do?menuNo=800212&cultureSeq={cultureSeq}`

## 파서

- 파일: `Crawler/Crawler_MunicipalYaml.py`
- 함수: `collect_palace_museum_education`
- 목록 구조: `ul.board-gallery.type5 > li`
- 상세 구조: `table`의 `교육명`, `교육일정`, `교육시간`, `교육장소`, `교육대상`, `접수일시`, `신청방법`, `참가비`
- 회차 구조: 상세 하단의 `교육일자`, `교육시간`, `교육신청기간`, `신청인원/정원`, `대기인원/정원`, `접수현황`

상세 페이지에 여러 회차가 있으면 회차별 신청상태와 정원이 다르므로 `cultureSeq:sessionNo` 기준으로 별도 row를 저장한다.

## 실행

```powershell
python -X utf8 Crawler\generated_yaml\NATIONAL_PALACE_MUSEUM.py --per-target-limit 0 --max-pages 5 --detail-limit 100 --timeout 20 --save-db --mark-stale
```

## 현재 검증 결과

- 수집: 42건
- 저장: 42건
- 페이지: 2
- 상세 요청: 15
- 필드 채움: title, period, schedule_raw, target, fee, description, image_url, venue_address 42/42
- DB active: 42건
- DB inactive: 기존 루트 URL generic 오수집 22건
- 상태 분포: `CLOSED` 36건, `WAITING` 6건

주의: 4건은 사이트가 정확한 회차 날짜 없이 `6월 목요일` 같은 월/요일 형태만 제공해서 `start_date` 변환이 되지 않는다.
