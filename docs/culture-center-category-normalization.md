# 문화센터 카테고리 정규화 기준과 크롤링 중 정제 방안

## 범위

이 문서는 문화센터 provider인 `HOMEPLUS`, `EMART`, `LOTTE`, `HYUNDAI_DEPT`, `GALLERIA`, `AK_PLAZA`, `ELAND_RETAIL`, `SHINSEGAE_ACADEMY`, `LOTTE_MART`를 대상으로 한다. 공공예약, 평생학습, 도서관, 체육시설 강좌는 같은 `courses` 테이블에 있어도 사이트 메뉴 구조와 분류 목적이 다르므로 이 기준에서 제외한다.

## 현재 데이터에서 확인한 문제

문화센터 크롤러들의 `category_raw`는 같은 의미 체계가 아니다.

| provider | 실제 `category_raw` 성격 | 예시 |
| --- | --- | --- |
| `HOMEPLUS` | 운영 구분 | `정규`, `단기`, `1일특강` |
| `EMART` | 사이트 메뉴, 연령, 일부 주제 혼합 | `With Mom`, `Kids & Children`, `Dance & Exercise`, `Home Cook` |
| `LOTTE` | 연령 구분 | `ADULT`, `CHILD`, `TODDLER`, `TEEN` |
| `HYUNDAI_DEPT` | 연령 구분 | `성인`, `유아`, `엄마랑 아가랑` |
| `AK_PLAZA` | 주제 메뉴 | `공예/플라워`, `쿠킹/베이킹`, `뮤직 라이프` |
| `GALLERIA`, `ELAND_RETAIL`, `LOTTE_MART`, `SHINSEGAE_ACADEMY` | 제목 중심 또는 데이터 부족 | `<missing>`, 복합 메뉴 문자열 |

따라서 `category_raw`를 그대로 표준 카테고리로 쓰면 성인/아동/정규 같은 값이 주제처럼 노출된다. 문화센터 표준 카테고리는 강좌 제목과 상세 설명을 우선하고, 사이트 메뉴는 보조 신호로만 사용해야 한다.

## 문화센터 표준 카테고리

규칙 파일은 `config/culture_center_standard_categories.yaml`이다.

| key | label | 주요 기준 |
| --- | --- | --- |
| `infant_play` | 영유아·놀이 | 오감, 감각, 트니트니, 아이좋아, 콩콩콩, With Mom |
| `art_craft` | 미술·공예 | 미술, 드로잉, 공예, 플라워, 가드닝, 소품, 재봉 |
| `music_instrument` | 음악·악기 | 피아노, 바이올린, 드럼, 해금, 가야금, 노래 |
| `dance_fitness` | 무용·댄스·운동 | 발레, 댄스, 요가, 필라테스, 스포츠 |
| `cooking_baking` | 요리·베이킹 | 요리, 쿠킹, 베이킹, 디저트, 음료 |
| `language_reading` | 어학·독서 | 영어, 외국어, 회화, 독서, 북리딩 |
| `science_creative` | 과학·창의 | 과학, 실험, 수학, 주판, 코딩, 로봇 |
| `digital_photo` | 디지털·사진 | 컴퓨터, 스마트폰, 사진, 영상, 미디어 |
| `beauty_life` | 뷰티·생활 | 메이크업, 네일, 미용, 화장품, 명리, 테라피 |
| `money_economy` | 재테크·경제 | 재테크, 경제, 금융, 부동산, 스마트스토어 |
| `certificate_professional` | 자격·전문 | 자격증, 창업, 강사, 지도사, 실무 |
| `humanities_culture` | 인문·전통문화 | 인문, 역사, 박물관, 문화재, 전통 |
| `hobby_leisure` | 취미·여가 | 바둑, 체스, 보드게임, 마술 |
| `experience_event` | 체험·이벤트 | 체험, 캠프, 탐방, 직업체험, 인형극 |
| `uncategorized` | 미분류 | 제목/상세에서 주제 근거가 부족한 항목 |

## 판정 원칙

1. 강한 신호: `title`, `title_raw`, `category_raw`, `program_type`.
2. 약한 신호: `collection_category`, `domain_category`, `source_group`, `description`.
3. `정규`, `단기`, `ADULT`, `CHILD`, `TODDLER`, `Kids & Children`, `With Mom`은 단독으로 최종 주제 카테고리가 되지 않는다.
4. 제목의 강한 주제 신호가 있으면 설명이나 메뉴에서 나온 약한 신호보다 우선한다. 예를 들어 `토요 바둑`은 설명에 `With Mom`이 있어도 `취미·여가`가 된다.
5. `AI`, `IT`, `PT` 같은 짧은 영문 키워드는 단어 단위로만 매칭한다. `makeup` 안의 `ai`처럼 부분 문자열로 오분류하지 않는다.
6. `미분류`는 실패가 아니라 보류 상태다. 로그인 페이지, 시간표 페이지, 제목이 `강좌`뿐인 행은 크롤러 필터링 대상이다.

## 크롤링 중 정제 방법

DB 스키마를 바꾸지 않는 전제에서는 다음 순서가 안전하다.

1. 각 크롤러가 원본 값을 그대로 보존한다.
   - `category_raw`: 사이트 원본 메뉴/연령/운영값.
   - `target_age_group`: 연령 파서 결과.
   - `collection_category`, `domain_category`: 계속 `문화센터`.
2. `save_course()`에서 제목 정제와 연령 파싱이 끝난 뒤 표준 카테고리를 계산한다.
   - 공통 함수: `classify_standard_category(course_data, "config/culture_center_standard_categories.yaml")`
   - 결과: `key`, `label`, `confidence`, `matched_terms`.
3. 저장 위치는 기존 스키마를 유지하려면 `ai_category`를 우선 후보로 쓴다.
   - `ai_category = result.label`
   - `ai_tags`가 JSON/배열로 운용 가능하면 `standard_category_key`, `matched_terms`, `confidence`를 함께 남긴다.
   - 스키마 변경이 허용되는 시점에는 `standard_category_key`, `standard_category_label`, `standard_category_confidence` 컬럼을 별도 추가하는 것이 더 명확하다.
4. 미분류 또는 낮은 신뢰도는 로그로 남긴다.
   - 예: `CATEGORY_NORMALIZE_LOW_CONF provider=HOMEPLUS branch=... url=... title=... raw=... result=미분류`
5. 강좌가 아닌 페이지는 저장 전에 차단한다.
   - 제목이 `로그인해주세요.`, `강좌`뿐인 행
   - URL이 `TimeTable`, `Basket`, `MyCultureCenter` 등 상세 강좌가 아닌 행
   - `provider_course_id`를 만들 수 없는 행

## 크롤러별 적용 위치

세 크롤러 모두 `save_course()` 안에서 제목 정제, 연령 파싱, lifecycle 계산 후 INSERT 전에 적용하는 것이 가장 작다.

| provider | 적용 위치 |
| --- | --- |
| `HOMEPLUS` | `Crawler/Crawler_Homeplus.py`의 `save_course()`, `enrich_course_lifecycle(course_data)` 직후 |
| `EMART` | `Crawler/Crawler_Emart.py`의 `save_course()`, `enrich_course_lifecycle(course_data)` 직후 |
| `LOTTE` | `Crawler/Crawler_Lotte.py`의 `save_course()`, `enrich_course_lifecycle(course_data)` 직후 |
| `HYUNDAI_DEPT`, `GALLERIA`, `AK_PLAZA`, `ELAND_RETAIL`, `SHINSEGAE_ACADEMY`, `LOTTE_MART` | `Crawler/Crawler_YamlSources.py`의 `save_course()` 공통 저장 직전 |

단, 현재 INSERT SQL은 `ai_category`를 넣지 않으므로 실제 크롤링 저장까지 연결하려면 세 파일의 INSERT/UPDATE 컬럼 목록에 `ai_category`를 추가해야 한다. 이 변경은 스키마 변경 없이 가능하지만, 기존 AI 처리 파이프라인이 `ai_category`를 쓰고 있으므로 적용 전 정책 결정이 필요하다.

## 검증 명령

```powershell
python -m py_compile tools\standard_category_mapper.py tools\audit_standard_categories.py
python -m unittest tests.test_standard_category_mapper
python tools\audit_standard_categories.py --culture-centers --config config\culture_center_standard_categories.yaml
```

감사 리포트는 `logs/standard_category_audits/` 아래에 JSON, CSV, Markdown으로 생성된다.

## 현재 검증 결과

2026-06-19 현재 활성 문화센터 전체 provider 기준 감사 결과:

- 대상: `HOMEPLUS`, `EMART`, `LOTTE`, `HYUNDAI_DEPT`, `GALLERIA`, `AK_PLAZA`, `ELAND_RETAIL`, `SHINSEGAE_ACADEMY`, `LOTTE_MART`
- scanned: `4,849`
- classified: `4,832`
- uncategorized: `17`
- 리포트: `logs/standard_category_audits/culture_center_category_audit_20260619_125631.json`

미분류 17건의 성격:

- `HOMEPLUS`: `로그인해주세요.`, `강좌`처럼 실제 강좌 상세가 아닌 페이지 4건.
- `GALLERIA`: 지점/로그인/카드/영업정보/개인결제창 등 강좌가 아닌 페이지 7건.
- `ELAND_RETAIL`: `강좌기간`처럼 파싱이 잘못된 행 3건.
- `LOTTE`: `데일리 프렌치 단기 클래스` 3건. 제목만으로 어학/공예/요리 여부를 단정하기 어려우므로 상세 설명 보강 전까지 `미분류`로 유지한다.

카테고리 기준으로 억지 분류할 대상은 없고, 비강좌 페이지는 크롤러 저장 전 필터에서 제외해야 한다.

연령/출처 정제 dry-run 결과:

- 명령: `python tools\apply_category_age_patterns.py`
- scanned: `4,849`
- changed: `5`
- applied: `0`
- 리포트: `logs/category_age_audits/category_age_patterns_dry_run_20260619_125623.json`

변경 후보는 `AK_PLAZA` 3건, `LOTTE_MART` 1건의 성인 기본 숫자 나이 제거와 `ELAND_RETAIL` 1건의 명시 연령 보정이다. 범위가 작고 규칙상 타당하므로 별도 적용해도 되지만, 이 문서 작업에서는 DB를 변경하지 않고 dry-run 근거만 남긴다.

## 적용 판단

전체 문화센터 크롤러에 카테고리 정규화 기준은 적용 가능하다. 다만 적용 순서는 다음이 안전하다.

1. 먼저 크롤러 저장 전 필터를 추가한다.
   - 제목이 `로그인해주세요.`, `강좌`, `강좌기간`뿐인 행 제외.
   - URL이 로그인, 카드, 지점 안내, 시간표, 장바구니, 개인결제창처럼 강좌 상세가 아닌 행 제외.
2. 이후 크롤링 저장 직전에 `classify_standard_category()`를 호출해 표준 카테고리를 계산한다.
3. DB 스키마를 바꾸지 않는다면 `ai_category`에 label을 저장할 수 있으나, 기존 AI 처리 파이프라인과 의미가 충돌할 수 있으므로 즉시 DB 반영은 보류한다.
4. 우선 API 조회 시 계산하거나 별도 배치 리포트로 검증한 뒤, `ai_category` 재사용 또는 전용 컬럼 추가 중 하나를 결정한다.

결론: 기준 자체는 전체 문화센터 크롤러에 적용 가능하다. 실제 DB 저장 적용은 비강좌 페이지 필터를 먼저 넣은 뒤, `ai_category` 사용 정책을 확정하고 진행한다.
