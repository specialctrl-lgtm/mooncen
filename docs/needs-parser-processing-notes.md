# needs_parser 처리 기록

생성형 YAML 크롤러 대상 중 `crawler_status: needs_parser` 상태를 샘플 수집 결과 기준으로 정리한다.

## 처리 기준

- 샘플 수집 결과가 있고 품질 등급이 A/B이면 `ready`로 전환한다.
- 샘플 수집 결과가 있지만 품질 등급이 C/D이면 `partial`로 전환한다.
- 샘플 수집이 실패했고 명확한 오류가 있으면 `blocked`로 전환한다.
- 공지, 안내, 상세글 URL처럼 수강신청 목록 URL이 아닌 경우 `needs_discovery`로 전환한다.
- 언론사, 블로그, 문서 파일, PDF/HWP 같은 비강좌 URL은 `deprecated.yaml`로 이동한다.

## 2026-06-04 처리 결과

기준 리포트:

```bash
logs/municipal_crawler_reports/municipal_yaml_crawler_20260604_183427.yaml
```

처리 전 active YAML 기준 `needs_parser`는 79건이었다. 샘플 실행 후 아래처럼 전환했다.

| 상태 | 건수 | 의미 |
| --- | ---: | --- |
| ready | 15 | 샘플 수집 품질 A/B |
| partial | 2 | 샘플 수집 가능하지만 필드 품질 약함 |
| needs_discovery | 55 | 예약/신청 목록 URL 재탐색 필요 |
| blocked | 1 | 접속 오류 |
| deprecated | 6 | 언론/문서 등 비강좌 URL |

처리 후 active YAML 기준 `needs_parser`는 0건이다.

## 실행 명령

샘플 리포트를 기준으로 dry-run:

```bash
python -X utf8 tools/process_needs_parser_targets.py --report logs/municipal_crawler_reports/municipal_yaml_crawler_20260604_183427.yaml --dry-run
```

실제 반영:

```bash
python -X utf8 tools/process_needs_parser_targets.py --report logs/municipal_crawler_reports/municipal_yaml_crawler_20260604_183427.yaml
```

레지스트리와 provider wrapper 재생성:

```bash
python -X utf8 Crawler/Crawler_GeneratedYamlTargets.py --write-registry
python -X utf8 tools/generate_registry_crawler_files.py
```

생성 wrapper 단건 테스트:

```bash
python -X utf8 Crawler/generated_yaml/HONAM_BIOLOGICAL_RESOURCES.py --limit 3 --max-pages 2 --detail-limit 3 --timeout 20
```

## 후속 작업

- `needs_discovery`는 URL 자체가 잘못된 경우가 많으므로 사이트명 + 예약/신청/교육/강좌 키워드로 목록 URL을 다시 찾아야 한다.
- `partial`은 전용 파서를 추가하거나 상세 페이지 필드 추출을 보강해서 `ready`로 승격한다.
- `blocked`는 접속 차단, 일시 장애, TLS/방화벽 문제를 분리해서 재시도한다.
