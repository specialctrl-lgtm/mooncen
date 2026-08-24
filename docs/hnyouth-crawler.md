# 하남시청소년수련관 크롤러

## 대상
- Provider: `MUNI_ONLINE_HNYOUTH_KR_6F390C33`
- URL: `https://online.hnyouth.kr/HnYouth`
- 분류: 청소년
- 수집 방식: AJAX 목록 + POST 상세

## 중복/안내 URL
- Provider: `MUNI_ONLINE_HNYOUTH_KR_A03457AE`
- URL: `https://online.hnyouth.kr/HnYouth/s_center/guide.php`
- 이 URL은 수강안내 페이지이며 실제 강좌 목록이 없다.
- generic parser가 안내 문구를 강좌로 오수집하던 기존 데이터 13건은 inactive 처리했다.
- 해당 target은 `deprecated` 및 `duplicate_of: MUNI_ONLINE_HNYOUTH_KR_6F390C33`로 관리한다.
- `online.hnyouth.kr`의 `/HnYouth` 외 경로는 `no_structured_courses`로 처리해 재오수집을 방지한다.

## 구현
- 목록 API: `s_center/jLecture_Search_List_202406.ajax.php`
- 상세 API: `s_center/pro_view.php`
- 목록은 `page` 파라미터로 페이지를 넘긴다.
- 상세는 목록 행의 `goLink(...)` 인자를 form 값으로 넘겨 POST 요청한다.
- `tr.jone`, `tr.tt`, `tr.cc:not(.xx)` 행을 강좌로 수집한다.
- branch는 `하남시청소년수련관` 단일 지점으로 저장한다.
- 강습장소는 course의 `venue_name`으로 저장한다.
- 주소는 `경기도 하남시 조정대로 111`로 저장한다.

## 수집 필드
- `title`: `goLink`의 `ntitle`
- `branch`: `하남시청소년수련관`
- `venue_name`: 강습장소
- `address`, `venue_address`: `경기도 하남시 조정대로 111`
- `period`: 상세 페이지의 강습기간
- `schedule_raw`: 상세 페이지의 강습시간
- `target`: 목록의 교육대상
- `fee`: 목록/상세의 수강요금
- `status`: `준비 -> 접수예정`, `마감 -> 접수마감`, `대기 -> 대기접수`
- `description`: 상세 페이지의 강좌소개/비고
- `material_note`: 상세 페이지의 준비물

## 실행 명령
```powershell
python -X utf8 Crawler\generated_yaml\MUNI_ONLINE_HNYOUTH_KR_6F390C33.py --per-target-limit 0 --max-pages 10 --detail-limit 100 --timeout 20
```

DB 저장 및 stale 정리:
```powershell
python -X utf8 Crawler\generated_yaml\MUNI_ONLINE_HNYOUTH_KR_6F390C33.py --per-target-limit 0 --max-pages 10 --detail-limit 100 --timeout 20 --save-db --mark-stale
```

## 검증 결과
- 실행 시각: `2026-06-05T00:31:48`
- 수집: 46건
- 저장: 46건
- 페이지: 4
- 상세: 46
- 필드 품질: `title`, `period`, `schedule_raw`, `fee`, `description`, `target`, `address` 모두 46/46
- 기존 오수집 13건은 `--mark-stale`로 inactive 처리됨
