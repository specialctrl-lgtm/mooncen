# 진천군평생학습관 읍면별 강좌 크롤러

## 대상

- Provider: `MUNI_WWW_JINCHEON_GO_KR_1CD1E7D2`
- URL: `https://www.jincheon.go.kr/jclll/sub.do?menukey=3237&mode=list&searchCnteduEmd=EMD5&searchKrwd=`
- 분류: 교육·체험 / 평생학습
- Crawler: `Crawler/generated_yaml/MUNI_WWW_JINCHEON_GO_KR_1CD1E7D2.py`

## 구현 내용

- 원래 URL은 `EMD5` 단일 읍면 필터지만, 읍면별 강좌 페이지 특성상 모든 읍면 필터를 순환한다.
- 순환 필터: `EMD1`, `EMD2`, `EMD09`, `EMD3`, `EMD4`, `EMD5`, `EMD6`, `EMD7`, `EMD8`
- 목록 `ul.tb_edu > li.item`에서 제목, 운영기관, 접수기간, 교육기간, 대상, 요일/시간, 정원, 장소, 교육비/재료비를 수집한다.
- 상세 `mode=view&cnteduNo=...`에서 강좌정보, 안내사항, 교육기관 정보, 지도용 주소를 보강한다.
- 지점명은 교육장소 기준으로 분리하되 `☏생거진천평생학습관-3 /강의실 307호` 같은 값은 `생거진천평생학습관`으로 정규화한다.
- 교육기간 종료일이 지난 강좌는 기본 수집에서 제외한다.

## 수집 필드

- `title`: 강좌명
- `branch`: 교육장소 기반 지점명
- `address`: 상세 지도 주소 또는 장소 괄호 주소
- `period`: 교육기간
- `schedule_raw`: 교육기간 + 요일/시간
- `target`: 교육대상
- `fee`: 교육비
- `material_fee`, `material_note`: 재료비
- `status`: 신청/교육 상태
- `description`: 강좌정보 및 안내사항
- `raw_url`: 상세 URL

## 검증

2026-06-09:

```bash
python -m py_compile Crawler/generated_yaml/MUNI_WWW_JINCHEON_GO_KR_1CD1E7D2.py
python -X utf8 Crawler/generated_yaml/MUNI_WWW_JINCHEON_GO_KR_1CD1E7D2.py --limit 10 --max-pages 8 --timeout 30
python -X utf8 run_crawlers.py --providers MUNI_WWW_JINCHEON_GO_KR_1CD1E7D2 --limit 10 --once --ignore-active-window --ignore-worker-lock --skip-coordinate-backfill --skip-category-backfill
```

결과:

- Collected: 10
- Saved: 10
- Quality: A / 100.0
- Required fields were filled for `title`, `branch`, `address`, `period`, `schedule_raw`, `target`, `fee`, `status`, `description`, and `raw_url`.

## 참고

- `EMD5` 단일 필터만 사용할 경우 현재 교육기간 미종료 강좌가 없어 0건이 된다.
- 전체 읍면 필터를 순환하면 현재 교육기간 미종료 강좌가 수집된다.
- 접수기간이 지난 강좌는 `CLOSED`로 저장하되, 교육기간이 지나지 않았으면 수집한다.
