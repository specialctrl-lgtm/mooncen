# AI Model Test Notes

## 2026-05-28

Ollama model was changed from `qwen3.5:4b` to `qwen3.5:9b`.

Validation:

- `GET http://victus:11434/api/tags` shows `qwen3.5:9b`.
- Direct Ollama JSON generation works with `qwen3.5:9b`.
- `qwen3.5:4b` is no longer available and returns 404.
- `AIProcessor.analyze_title` correctly extracted:
  - `24~48개월` -> `TODDLER`, `24~48`
  - `24개월 이상` -> `TODDLER`, `24~null`
- `AIProcessor.analyze_course` produced a usable Korean summary and tags.

Configuration updated:

- `.env`
- `deploy.local.ps1`

Observation:

- The first direct model call took about 37 seconds due to model load.
- Warm title extraction calls took about 18 to 26 seconds each.
- Summary/tag extraction took about 11 seconds.

## 2026-05-28 Host Split

Development uses the direct Victus IP:

- `.env`: `OLLAMA_HOST=http://172.28.3.157:11434`

Production deployment keeps the hostname because the Ubuntu server can resolve it:

- `deploy.local.ps1`: `MoonCenOllamaHost=http://victus:11434`

The local Ops Console now reads Ollama host/model from `.env` for AI checks. Deployment still uses `deploy.local.ps1`.

## 2026-05-28 AI Title Age Fragment Cleanup

Problem:

- AI sometimes returned a normalized `target_text` such as `2018~2021년생`.
- The generated `clean_title` could still contain the original short form, such as `2018~21년생`.
- Exact string removal did not match those two forms, so age text remained in the display title.

Change:

- `ai_processor.py` now removes age fragments using normalized and source-title variants.
- Birth-year ranges support full and short forms, for example `2018~2021년생`, `2018~21년생`, and hyphen variants.
- Bracketed age metadata such as `[2020~22년생/일/10:00]` is removed without leaving broken schedule text.

Validation:

- `로비특강|K-컬쳐 2018~21년생 (케이팝 캐릭터 슈링클스 열쇠고리)` becomes `로비특강|K-컬쳐 (케이팝 캐릭터 슈링클스 열쇠고리)`.
- `아이돌 댄스 따라잡기(포인트 안무)(2017~21년생)` becomes `아이돌 댄스 따라잡기(포인트 안무)`.
- `K-POP Star G.Den [2020~22년생/일/10:00]` becomes `K-POP Star G.Den`.

## 2026-05-29 AI Reprocess And Quality Report

AI processing can now be restarted from the beginning.

Commands:

- Safe reset, preserving existing crawler target/age fields:
  `python -X utf8 run_ai_pipeline.py --reset-ai --reset-only`
- Reset and process immediately in the current process:
  `python -X utf8 run_ai_pipeline.py --from-scratch --once --limit 20 --ignore-active-window`
- Full reset including target/age fields:
  `python -X utf8 run_ai_pipeline.py --reset-ai --reset-target-fields --reset-only`

Production helpers:

- `mooncenctl ai-reset-start`
- `mooncenctl ai-reset-full-start`
- `mooncenctl ai-quality`

Ops Console:

- `AI Manual Control` has `Reset AI + start`, `Full reset + start`, and `AI quality report`.
- `AI Work` also exposes `Reset AI + start` and `AI quality report`.

Quality report script:

`python -X utf8 tools/ai_quality_report.py --active-only`

The report checks:

- AI summary/tag coverage
- AI title/age coverage
- summary/tag/category fill rate
- target text, age group, and month-bound fill rate
- low title confidence
- remaining age text in cleaned title
- remaining schedule/date/time text in cleaned title
- bad titles such as menu/search/system placeholder text
- explicit age text without age group
- adult courses with min/max age fields
- too-short summaries
- processed rows with empty tags

Local validation on active courses:

- Total active rows: `20,646`
- Summary processed: `87`
- Title processed: `137`
- Low-confidence processed titles: `0`
- Remaining age text in processed titles: `0`
- Remaining schedule text in processed titles: `0`

## 2026-06-06 Title Unchanged Policy

If AI title splitting returns the same `clean_title` as the current DB `courses.title`, MoonCen does not apply the AI title result.

- The row keeps `ai_title_processed = false`, so frontend and SEO display do not treat the title as AI-cleaned.
- To avoid retry loops, `ai_title_result.source` is stored as `title_unchanged`.
- `run_ai_pipeline.py` excludes `title_unchanged` rows from the title-processing fetch queue.
- Ops Console and `tools/ai_quality_report.py` report `title_unchanged` separately from `title_processed` and `title_pending`.

## 2026-06-19 AI Quality And Fast Title Path

AI post-processing was tightened after measuring live samples.

- Clear title age targets such as `2020~2022년생`, `55세 이상`, and `2017~21년생` now use a rule fast path before model calls.
- Fast path keeps the existing month-based age contract and records `ai_title_result.source=rule_fast_path`.
- Target text is normalized, for example `2017~21년생` becomes `2017~2021년생`.
- Category post-processing now prefers strong rule-based categories over weak but valid AI categories.
- Summary post-processing rejects title-repeat summaries and uses description-based fallback when the AI output repeats the title or includes English tokens.
- Fallback result generation also prefers description-based summaries, so parse failures or long English AI summaries do not save the course title as the summary.
- Korean fallback words, noise filters, category rules, and fallback tags were replaced with encoding-safe Unicode escape rules.

Measured local samples:

- Before: 3 samples took `71.58s`, average `23.86s/course`.
- After: 3 samples took `26.40s`, average `8.80s/course`.
- Title/age step after fast path: total `0.0534s` for 3 samples.

Sample outputs after the change:

- LOTTE: `동그라미 창의미술`, target `2020~2022년생`, age `TODDLER 48~72`, category `Art`.
- HOMEPLUS: `척추와 관절에 좋은 시니어 요가 (추천) (1차/6회)`, target `55세 이상`, age `ADULT 660~`, category `Fitness`.
- EMART: `K-POP 방송댄스`, target `2017~2021년생`, age `CHILD 60~108`, category `Fitness`, summary `아이들이 최신 안무를 배우며 리듬감과 표현력을 키웁니다`.

Final UTF-8 sample check after fallback correction:

- LOTTE: title `동그라미 창의미술`, target `2020~2022년생`, age `TODDLER 48~72`, category `Art`, summary `다양한 재료로 상상력 기르는 유아 미술`.
- HOMEPLUS: title `척추와 관절에 좋은 시니어 요가 (추천) (1차/6회)`, target `55세 이상`, age `ADULT 660~`, category `Fitness`, summary `시니어용 저강도 요가 수업을 통해 유연성과 균형을 키웁니다`.
- EMART: title `K-POP 방송댄스`, target `2017~2021년생`, age `CHILD 60~108`, category `Fitness`, summary `최신 음악에 맞춰 기본 리듬감과 안무를 익히는 어린이 방송댄스`.

Validation:

- `python -m unittest discover tests`
- `python -m py_compile ai_processor.py run_ai_pipeline.py`

## 2026-06-19 wtr-linux Qwen3 8B Target

MoonCen AI defaults now target the Ollama service on `wtr-linux`.

- Runtime/default host: `OLLAMA_HOST=http://wtr-linux:11434`
- Runtime/default model: `OLLAMA_MODEL=qwen3.5:9b`
- Updated local `.env`, deploy scripts, Ubuntu env example, AI helper scripts, and Ops Console defaults.
- Ops Console AI Work now reports the remote AI worker's Ollama host/model, installed model list, and whether `qwen3.5:9b` is present.
- Ops Console `Ollama qwen3.5:9b test` runs from the active server, not from the local browser host, so it verifies the same network path the production AI worker uses.

Local verification note:

- `wtr-linux` resolves to `wtr-linux.dinosaur-piano.ts.net` / `100.117.162.75` in this environment.
- Direct local HTTP to `http://wtr-linux:11434/api/tags` failed here, so final model-present verification must be done through Ops Console or an active-server SSH session.
- The previous Ollama endpoint `http://172.28.3.157:11434` still returned only `qwen3.5:9b` during local probing and is no longer the configured target.

Deployment verification:

- Deployed with `deploy-skip-workers` through Ops Console job `15293fc4074f`.
- Production build and health checks passed.
- The deployment check sourced `/opt/mooncen/.env` and attempted `http://wtr-linux:11434/api/tags` with model `qwen3:8b`.
- Result: `ollama_check={"host": "http://wtr-linux:11434", "model": "qwen3:8b", "ok": false, "model_present": false, "models": [], "error": "URLError: <urlopen error [Errno 111] Connection refused>"}`.
- This proves the active server is configured to use `wtr-linux`/`qwen3:8b`, but the Ollama service is not reachable from the active server yet.

Follow-up deployment verification:

- Deployed again with `deploy-skip-workers` through Ops Console job `d0cf6ee16485`.
- Added deploy-time Ops Console verification.
- Production build and health checks passed again.
- Ollama result is unchanged: `ollama_check={"host": "http://wtr-linux:11434", "model": "qwen3:8b", "ok": false, "model_present": false, "models": [], "error": "URLError: <urlopen error [Errno 111] Connection refused>"}`.

Firewall-off recheck:

- `wtr-linux` still resolves to `100.117.162.75`.
- ICMP ping to `wtr-linux` succeeds with about `1ms`, so the Tailscale route is reachable.
- TCP check to `wtr-linux:11434` still fails.
- `http://wtr-linux:11434/api/tags` and `http://100.117.162.75:11434/api/tags` still fail with connection errors.
- SSH port 22 is reachable, but key authentication failed for `administrator`, `sgm`, `ubuntu`, and `root`, so this session cannot inspect or restart Ollama on `wtr-linux` directly.

Ollama listen fix recheck:

- After `OLLAMA_HOST=0.0.0.0:11434` was applied on `wtr-linux`, TCP check to `wtr-linux:11434` succeeded.
- `http://wtr-linux:11434/api/tags` returned `qwen3:8b`.
- The model reports family `qwen3`, parameter size `8.2B`, quantization `Q4_K_M`, context length `40960`, and digest `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`.
- Direct `/api/generate` with `qwen3:8b` returned `{"ok":true}`.
- Local `AIProcessor` initialized with endpoint `http://wtr-linux:11434/api/generate`, model `qwen3:8b`, and returned `{"ok":true}`.
- A follow-up deploy-based production check could not reach the Ollama check step because setup failed first with PostgreSQL contention:
  - job `515ee7ce4e49`: `deadlock detected`
  - job `4035ff2b6e96`: `remaining connection slots are reserved for roles with the SUPERUSER attribute`
- Existing Ops Console `ollama-test` action returned `{"ok":true}`, but that local process is an older version and does not print host/model, so the stronger production-path proof remains pending until DB contention clears or current Ops Console can be queried directly.
