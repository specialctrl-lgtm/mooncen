# 용산구문화체육센터 FMCS 크롤러

## 대상

- Provider: `MUNI_YSSPORTS_YONG_SAN_OR_KR_67C8F87B`
- 실제 수집 URL: `https://yssports.yong-san.or.kr/fmcs/8?center=YGSN01`
- 기존 후보 URL `https://yssports.yong-san.or.kr/www/50`은 할인/안내 페이지라 강좌 수집 대상이 아니다.

## 구현 방식

- `Crawler/Crawler_MunicipalYaml.py`에 `collect_yssports_fmcs_categories` 전용 파서를 추가했다.
- `rest/common/company`와 `rest/common/category`를 호출해 센터와 분류 구조를 순환한다.
- `rest/lecture/list`는 표준 FMCS 파라미터(`company_code`, `category_cd`)가 빈 결과를 반환하므로, 이 사이트의 legacy 응답을 가져온 뒤 `comcd`, `category1`, `category2` 기준으로 센터/분류를 보정한다.
- 상세 페이지는 `dl/dt/dd` 구조라서 FMCS 공통 상세 파서에 `proc_read dl` 추출을 추가했다.

## 수집 필드

- `title`, `branch`, `branch_code`, `address`, `period`, `schedule_raw`, `fee`, `target`, `status`, `raw_url`
- `description`은 상세 페이지에 강좌 소개 본문이 없으면 비워둔다.
- 지도 표시를 위해 센터별 주소를 고정 매핑한다.

## 검증

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_YSSPORTS_YONG_SAN_OR_KR_67C8F87B.py --per-target-limit 10 --max-pages 2 --detail-limit 10 --timeout 30
python -X utf8 tools\report_municipal_crawler_quality.py --report logs\municipal_crawler_reports\municipal_yaml_crawler_20260605_110101.yaml --limit 15
```

2026-06-05 샘플 10건 결과:

| 항목 | 결과 |
| --- | --- |
| 수집 건수 | 10 |
| 품질 등급 | A |
| 핵심 필드 | 100.0% |
| 중요 필드 | 83.3% |
| 기간 | 100.0% |
| 일정 | 100.0% |
| 수강료 | 100.0% |
| 대상 | 100.0% |
| 설명 | 0.0% |
