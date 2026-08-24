# Branch Split Crawler Policy

지도에 표시해야 하는 수집 대상은 course row마다 실제 지점/기관 단위의 `branch`를 가져야 한다.

## 기준

- 지점이 있는 사이트는 provider 대표명이나 시군구명을 `branch`로 저장하지 않는다.
- 목록이나 상세에 `운영기관`, `기관명`, `시설명`, `센터명`, `지점`, `지점명`, `도서관명`, `복지관명`이 있으면 그 값을 `branch`로 저장한다.
- `장소`, `교육장소`, `행사장소`, `강의실`, `위치`는 `venue_name`으로 보존한다. 단, provider/branch가 시군구나 통합예약 같은 넓은 이름이고 실제 시설명이 `venue_name`에만 있으면 저장 단계에서 시설명을 branch로 승격한다.
- 주소가 있으면 `venue_address`, `address`, `place_address` 중 확보 가능한 값을 저장한다. 지점 좌표는 이후 Kakao Local address fix로 보정한다.
- 한 사이트 안에 여러 기관 또는 지점 URL이 있으면 YAML target에 URL을 여러 개 등록하거나, 전용 파서에서 지점 목록을 순회한다.

## 구현 위치

- 공통 라벨 인식: `Crawler/Crawler_MunicipalYaml.py`
- DB 저장 시 broad branch 승격: `Crawler/Crawler_MunicipalYaml.py`의 `MunicipalDbWriter.branch_info_from_row`
- 지점 분리 누락 후보 리포트: `tools/report_branch_split_candidates.py`

## 점검 명령

개발 DB 기준:

```powershell
cd C:\project\mooncen
python -X utf8 tools\report_branch_split_candidates.py --min-active 20 --limit 200 --write
```

운영 서버 기준:

```bash
cd /opt/mooncen
sudo -u mooncen /opt/mooncen/.venv/bin/python -X utf8 tools/report_branch_split_candidates.py --min-active 20 --limit 200 --write
```

리포트는 `logs/crawler_dev_reports/branch_split_candidates_*.md`와 `.csv`에 저장된다.

## 처리 우선순위

1. `risk=HIGH`이고 `reason=broad branch name`인 provider부터 전용 파서 또는 URL 분리 target을 만든다.
2. `branch_id missing`은 저장 단계 또는 row 생성 단계 오류이므로 우선 수정한다.
3. `risk=REVIEW`는 실제 단일 기관인지 확인한다. 단일 기관이면 유지하고, 여러 지점이 숨어 있으면 분리한다.
4. 지점 분리 후 주소/좌표가 비어 있으면 address fix를 실행한다.

## 합격 기준

- provider의 활성 강좌가 여러 기관에 걸쳐 있으면 `active_branches`가 2 이상이어야 한다.
- 지점별 강좌가 지도와 목록에서 같은 지점명으로 묶여야 한다.
- 주소가 없는 지점은 Ops Console의 주소 보정 대상에 나타나야 한다.
