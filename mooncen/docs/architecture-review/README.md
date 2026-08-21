# MoonCen 아키텍처·개선·운영 문서 패키지

작성 기준일: 2026-08-19 (UTC)  
분석 기준: 현재 워크스페이스 스냅샷, `master@8d55e873bfb06ec33f566839fce7ee98650955f8`

## 문서 목록

| 문서 | 대상 독자 | Markdown | Word |
|---|---|---|---|
| 아키텍처 간략본 | 경영진·PO·신규 참여자 | `MoonCen_아키텍처_간략본.md` | `MoonCen_아키텍처_간략본.docx` |
| 아키텍처 상세본 | 개발·데이터·보안·인프라 담당자 | `MoonCen_아키텍처_상세본.md` | `MoonCen_아키텍처_상세본.docx` |
| 개선방안·우선순위 간략본 | 의사결정자·로드맵 담당자 | `MoonCen_개선방안_우선순위_간략본.md` | `MoonCen_개선방안_우선순위_간략본.docx` |
| 개선방안·우선순위 상세본 | 개선 과제 실행 담당자 | `MoonCen_개선방안_우선순위_상세본.md` | `MoonCen_개선방안_우선순위_상세본.docx` |
| 운영 가이드 | 운영·당직·개발·보안 담당자 | `MoonCen_운영_가이드.md` | `MoonCen_운영_가이드.docx` |

## 다이어그램

- `assets/01-current-architecture.svg`: 현재 저장소가 선언하는 시스템 구성
- `assets/02-crawler-flow.svg`: 현재 legacy 수집과 차단된 분산 목표 구조
- `assets/03-operations-loop.svg`: 권장 운영 순환 구조
- 같은 이름의 PNG: Word 삽입용 렌더링 산출물
- `SHA256SUMS`: 전달받은 Word·PNG 파일의 무결성 확인값

## 해석 시 주의사항

1. 분석 시작 시 작업 트리에 983개의 변경 상태 항목이 있었습니다. 이 문서는 Git HEAD가 아니라 **현재 워크스페이스 스냅샷**을 분석한 결과입니다.
2. 저장소 파일은 원하는 상태를 설명할 수 있지만 실제 서버 배포 상태를 증명하지 않습니다. 배포 commit, systemd 상태, DB role, Cloudflare Access, 백업 영수증은 운영 환경에서 별도로 확인해야 합니다.
3. `config/production_topology.json`은 `crawlerMode: legacy`와 모든 분산 worker의 비활성 상태를 선언합니다. 분산 크롤러 코드는 목표 기능이며 현재 운영 중이라고 해석하면 안 됩니다.
4. `deploy/ha`의 `n100` 기반 문서와 최신 토폴로지 문서는 충돌합니다. 정합성 복구 전에는 과거 HA 명령을 실행하지 않는 것이 안전합니다.

## 재생성

Word 파일과 PNG는 이 디렉터리의 `build_docs.py`로 생성합니다. 이 스크립트는 Markdown 원본과 SVG를 변경하지 않습니다. 기본 의존성은 `requirements-docs.txt`에 고정했으며, CairoSVG와 시스템 `libcairo`가 함께 있으면 SVG를 직접 렌더링하고 그렇지 않으면 포함된 deterministic Pillow renderer를 사용합니다.

```bash
python3 -m pip install -r requirements-docs.txt
python3 build_docs.py
```

Word·PNG를 다시 생성하면 `sha256sum *.docx assets/*.png > SHA256SUMS`와 동등한 방법으로 무결성 파일도 갱신해야 합니다.

