package com.mooncen.monitor;

final class PowerPolicy {
    static final long FOREGROUND_REFRESH_INTERVAL_MS = 5 * 60_000L;
    static final long RESUME_REFRESH_FRESHNESS_MS = 60_000L;
    static final long BACKGROUND_CORE_REUSE_MS = 60_000L;
    static final long BACKGROUND_POLL_INTERVAL_MS = 30 * 60_000L;
    static final long BACKGROUND_POLL_FLEX_MS = 15 * 60_000L;
    static final long AUTOMATIC_UPDATE_CHECK_INTERVAL_MS = 24 * 60 * 60_000L;
    static final long STATUS_REPUBLISH_INTERVAL_MS = 60 * 60_000L;
    static final int MAX_RESPONSE_BODY_BYTES = 512 * 1024;

    private PowerPolicy() {
    }

    static boolean isAutomaticUpdateCheckDue(long lastAttemptAt, long now) {
        return isIntervalDue(lastAttemptAt, now, AUTOMATIC_UPDATE_CHECK_INTERVAL_MS);
    }

    static boolean isForegroundRefreshDue(long lastCompletedAt, long now) {
        return isIntervalDue(lastCompletedAt, now, RESUME_REFRESH_FRESHNESS_MS);
    }

    static boolean isBackgroundCoreReuseFresh(long cachedAt, long now) {
        return cachedAt > 0
                && now >= cachedAt
                && now - cachedAt < BACKGROUND_CORE_REUSE_MS;
    }

    static boolean shouldPublishStatus(
            boolean initialized,
            boolean sameContent,
            long lastPublishedAt,
            long now
    ) {
        return !initialized
                || !sameContent
                || isIntervalDue(lastPublishedAt, now, STATUS_REPUBLISH_INTERVAL_MS);
    }

    private static boolean isIntervalDue(long previous, long now, long interval) {
        return previous <= 0 || now < previous || now - previous >= interval;
    }
}
