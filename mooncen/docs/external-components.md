# External runtime components

MoonCen keeps the web/API application and its read-only operational control
surface in this repository. Two host runtimes now have independent release and
CI authority:

| Component | Repository | Runtime owner | MoonCen boundary |
| --- | --- | --- | --- |
| Operations Telegram bot | `specialctrl-lgtm/mooncen-bot` | dedicated bot service | status only; no deployment coupling |
| Legacy crawler | `specialctrl-lgtm/mooncen-crawler-legacy` | `gen1crawler` | authenticated status and `crawler-once` request |

New bot and crawler runtime changes must be made and released from their own
repositories. Copies still present here are transition-only dependencies for
the old deployment and crawler-analysis code; they are not release authority.
They will be removed as those consumers are replaced with versioned external
artifacts or read-only API contracts.

Initial extraction provenance is MoonCen commit `4e2e9f2`.
