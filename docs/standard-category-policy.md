# 표준 카테고리 분류 기준

현재 DB의 카테고리성 값은 서로 다른 의미가 섞여 있다.

- 출처/기관 분류: `문화센터`, `평생학습`, `공공예약`, `도서관`, `복지관`
- 사이트 메뉴나 운영 값: `정규`, `단기`, `선착순`, `추첨식`, `전체`
- 대상 연령 값: `ADULT`, `CHILD`, `TODDLER`, `With Mom`, `Kids & Children`

따라서 표준 카테고리는 `collection_category/domain_category`를 대체하지 않고, 강좌의 실제 주제를 별도로 분류한다. 출처, 운영 방식, 연령대만으로는 최종 주제 카테고리를 확정하지 않는다.

## 표준 카테고리

| key | label | 기준 |
| --- | --- | --- |
| `infant_play` | 영유아·놀이 | 영유아, 부모 동반, 오감, 감각, 놀이 |
| `art_craft` | 미술·공예 | 미술, 공예, 만들기, 도예, 서예, 플라워 |
| `music_performance` | 음악·공연 | 악기, 노래, 음악, 공연, 연극 |
| `sports_fitness` | 운동·스포츠 | 체육, 수영, 댄스, 발레, 요가, 필라테스 |
| `cooking_food` | 요리·베이킹 | 요리, 베이킹, 디저트, 음료 |
| `digital_it` | 디지털·IT | 컴퓨터, 스마트폰, 코딩, AI, 정보화 |
| `language_humanities` | 어학·인문 | 외국어, 독서, 글쓰기, 인문, 스피치 |
| `career_license` | 직업·자격 | 취업, 자격증, 창업, 강사 양성 |
| `science_creative` | 과학·창의 | 과학, 실험, 수학, 창의, 드론 |
| `hobby_leisure` | 취미·여가 | 바둑, 보드게임, 취미형 여가 활동 |
| `nature_ecology` | 자연·생태 | 숲, 생태, 환경, 식물, 농업 |
| `culture_history` | 역사·전통문화 | 박물관, 전시, 문화유산, 전통 |
| `experience_trip` | 체험·견학 | 체험, 견학, 캠프, 현장학습 |
| `health_life` | 건강·생활 | 건강, 치유, 생활관리, 경제 |
| `civic_public` | 시민·공공교육 | 시민참여, 주민, 안전, 공공교육 |
| `uncategorized` | 미분류 | 규칙으로 판단할 근거가 부족한 항목 |

## 판정 순서

1. `title/title_raw`, `category_raw`, `program_type`을 강한 신호로 본다.
2. `collection_category`, `domain_category`, `source_group`, `description`은 보조 신호로만 쓴다.
3. `정규`, `단기`, `선착순`, `추첨식`, `ADULT`, `CHILD` 같은 값은 최종 주제 카테고리로 쓰지 않는다.
4. 여러 카테고리가 동시에 매칭되면 우선순위와 강한 신호 매칭 수가 높은 쪽을 선택한다.
5. 매칭 근거가 없으면 `미분류`로 남긴다. 이 항목은 crawler/parser 또는 키워드 규칙 보강 대상으로 본다.

## 관련 파일

- 규칙: `config/standard_categories.yaml`
- 분류 함수: `tools/standard_category_mapper.py`
- 현재 DB 감사: `tools/audit_standard_categories.py`
- 테스트: `tests/test_standard_category_mapper.py`

## 실행

```powershell
python tools\audit_standard_categories.py
python tools\audit_standard_categories.py --provider HOMEPLUS,EMART,LOTTE
```

보고서는 `logs/standard_category_audits/`에 JSON, CSV, Markdown으로 생성된다. CSV는 엑셀에서 바로 열 수 있도록 UTF-8 BOM을 포함한다.
