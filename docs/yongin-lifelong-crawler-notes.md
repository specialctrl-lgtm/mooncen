# Yongin Lifelong Learning Crawler Notes

## Source URLs

The Yongin main lifelong learning site should not be crawled from the landing page only.

Use these list pages for the Yongin main center:

- Regular courses: `https://lll.yongin.go.kr/yongin/rgEdu/list.do`
- Irregular/lifelong courses: `https://lll.yongin.go.kr/yongin/irrgEdu/list.do?gbn=1&seq=23`
- Sports center registration: `https://llsports.yongin.go.kr/m04/1/index.asp`

The crawler adds `pitem=100` when requesting the lists so each page returns a larger batch.
The sports center URL currently redirects to `https://llsports.yongin.go.kr/m06/1/login.asp?loc=m04`, so it is recorded as a login-required source.

## Implementation

- Provider: `YONGIN_LIFELONG_LEARNING`
- Script: `Crawler/Crawler_YonginLifelong.py`
- Category YAML: `config/crawl_targets/lifelong_learning.yaml`
- The site has legacy TLS behavior in Python requests, so the crawler keeps the existing `curl -k -L` fallback.
- The sports center list path is kept in the crawler, but it is skipped as login-required until a non-login list or authenticated collection flow is defined.

## Latest Local Check

Command:

```powershell
python -X utf8 Crawler\Crawler_YonginLifelong.py --max-pages 1 --no-detail
```

Result on 2026-05-27:

- Collected: 379
- Pages: 9
- Yongin main regular courses: 66
- Yongin main irregular/lifelong courses: 98
- Field counts: title 379, branch 379, raw_url 379, status 379, schedule_raw 379, period 379

For fee and description quality checks, run without `--no-detail`.
