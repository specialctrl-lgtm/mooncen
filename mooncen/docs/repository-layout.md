# Repository layout

MoonCen keeps runtime code, operational control code, and reference data in
separate top-level directories. One-off Codex prompt launchers and the retired
native Android prototype are intentionally not part of the maintained tree.

| Path | Purpose | Production archive |
| --- | --- | --- |
| `backend/` | FastAPI application and public/Ops APIs | yes |
| `Crawler/` | provider collectors and generated wrappers | yes |
| `DB/` | canonical schema, migrations, and write guards | yes |
| `deploy/` | Ubuntu services, deployment, backup, and monitoring | yes |
| `ops_agent/` | bounded deployment/crawler worker control plane | yes |
| `tools/` | reviewed maintenance and operational CLI tools | yes |
| `utils/` | shared parsing, quality, URL, and safety helpers | yes |
| `frontend2/` | primary Kakao-map web client | yes (built artifact) |
| `ops-console/` | standalone authenticated operations console | yes (built artifact) |
| `mooncen-app/` | Expo mobile client | no |
| `frontend/` | deliberately non-buildable retired Google-map client contract | no |
| `tests/` | Python/unit/contract tests | no |
| `document/` | reviewed source spreadsheets retained as reference inputs | when explicitly needed |

Root-level scripts are limited to supported developer launchers and the main
crawler/AI entry points. Temporary probes, screenshots, database dumps, local
server inventories, generated logs, virtual environments, and private keys are
ignored and must never enter a release archive.
