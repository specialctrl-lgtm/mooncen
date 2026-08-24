# Functional Testing

MoonCen has a production functional test runner at `tools/ops/functional_test.py`.

It checks:

- local API health
- public API health through nginx/domain
- frontend root response
- database course/branch counts
- provider, course list, and course detail APIs
- child age filter API
- the Lotte Mart `4~6세` age regression case

## Production schedule

`mooncen-functional-test.timer` runs once per day:

```bash
systemctl status mooncen-functional-test.timer
```

Reports are written to:

```text
/opt/mooncen/logs/functional_tests/latest.json
/opt/mooncen/logs/functional_tests/functional_test_YYYYmmdd_HHMMSS.json
```

If `MOONCEN_BOT_TOKEN` and `MOONCEN_BOT_CHAT_ID` are configured, failed runs send a Telegram message.

## Manual run

From an operator machine:

```powershell
.\deploy_mooncen.ps1 functional-test -Target cloud
.\deploy_mooncen.ps1 functional-test-status -Target cloud
```

On the server:

```bash
mooncenctl functional-test
mooncenctl functional-test-status
```

For API-only local checks:

```bash
python -X utf8 tools/ops/functional_test.py \
  --base-url https://mooncen.kr \
  --internal-api-url https://mooncen.kr \
  --no-db \
  --no-notify
```
