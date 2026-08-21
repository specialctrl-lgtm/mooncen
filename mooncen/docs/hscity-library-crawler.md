# 화성시립도서관 강좌 수집

## 소유 Provider

- 운영 Provider: `MUNI_YEYAK_HSCITY_GO_KR_2DFD650A`
- 중복 방지용 비활성 별칭: `MUNI_YEYAK_HSCITY_GO_KR_E7FCC3C0`
- 공식 지점 목록: `https://www.hscitylib.or.kr/intro/menu/10002/contents/40024/contents.do`

## 수집 방식

공식 지점 목록에서 확인한 21개 공공도서관과 작은도서관 통합 메뉴를 포함해 22개 `강좌신청` 목록을 순회한다. 각 목록은 다음 형태다.

```text
https://www.hscitylib.or.kr/{slug}/menu/{menuId}/program/30021/lectureList.do
```

지점 목록에서는 다음 현재 상태만 추출한다.

- `ready`: 접수예정
- `apply`: 접수중
- `wait`: 대기접수중
- `finish`: 접수마감

`강좌종료`, `강좌취소`는 현재 스냅숏에서 제외한다. 지점 목록의 `lectureIdx` 집합과 화성 통합예약 전체 목록의 집합을 대조하고, 지점 강좌가 통합 목록에서 누락되면 저장을 차단한다.

## 상세 필드

통합예약 상세 `lectureDetail.do?lectureIdx=...`에서 다음 필드를 보강한다.

- `period`: `수강기간`
- `schedule_raw`: `요일/시간`
- `target`: `교육대상`
- `fee`: `수강료`
- `material_fee`: `재료비`
- `instructor`: `강사명`
- `description`: `.detail-tab.info-tab`
- `image_url`: `/attach/editor/...` 이미지

## 검증 결과

2026-07-28 실시간 검증 기준:

- 지점 목록 페이지: 23개(디렉터리 1 + 강좌 목록 22)
- 지점 목록 오류: 0개
- 현재 도서관 강좌: 186건
- 강좌가 있는 경로: 21개
- 두빛나래어린이도서관: 현재 강좌 0건
- 대상·요금·날짜·장소·분야·시간: 각각 186/186

## 실행

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_YEYAK_HSCITY_GO_KR_2DFD650A.py --per-target-limit 0 --max-pages 80 --detail-limit 1200 --timeout 30 --save-db --mark-stale
```
