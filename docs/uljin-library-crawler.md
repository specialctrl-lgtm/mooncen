# 울진군통합도서관 크롤러

## 대상
- Provider: `MUNI_LIB_ULJIN_GO_KR_84BA0199`
- URL: `https://lib.uljin.go.kr/content/edusat/list.php?sh_edu_lib=울진남부도서관`
- 분류: 도서관
- Parser: `uljin_library_edusat`

## 구현 내용
- `edusat/list.php`의 모든 도서관 지점 탭을 순회한다.
- 강좌 상세는 table이 아니라 `#edusatbox .basic dl` 구조라 `dt/dd`에서 대상, 모집인원, 수강료, 모집방법, 강사, 접수기간, 강의기간, 강의일시, 강의실을 수집한다.
- `edusat3/list.php` 도서관행사 온라인 신청도 같은 신청 데이터로 함께 수집한다.
- `edusat3` 상세는 table 구조라 `th/td`에서 참가대상, 행사장소, 행사기간, 접수기간, 접수상태, 행사설명을 수집한다.
- `무료(재료비...)` 형태는 수강료 0원, 재료비 별도 금액으로 분리되도록 처리한다.
- `수요일(16:30~17:30)`, `월~목16:00 ~ 16:40` 같은 일정 표기를 `수 16:30 ~ 17:30` 형태로 정규화한다.

## 2026-06-05 실행 결과
- 수집: 22건
- DB 저장: 6건
- 활성 강좌: 6건
- 기존 오수집 데이터 비활성화: 11건
- 활성 지점: 2개 (`북울진도서관`, `기성작은도서관`)
- 필드 채움: title/branch/status/period/target 22/22, schedule 20/22, fee 19/22, description 6/22
- 활성 데이터 기준: status/fee/target 6/6, schedule 5/6, description 4/6
- 리포트: `logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_001154.yaml`

## 실행 명령
```bash
python -X utf8 Crawler/generated_yaml/MUNI_LIB_ULJIN_GO_KR_84BA0199.py --save-db --mark-stale
```
