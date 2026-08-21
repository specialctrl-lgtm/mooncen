package com.mooncen.monitor;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class PowerPolicyTest {
    @Test
    public void automaticUpdateChecksAreLimitedToOncePerDay() {
        long now = 10 * PowerPolicy.AUTOMATIC_UPDATE_CHECK_INTERVAL_MS;
        assertTrue(PowerPolicy.isAutomaticUpdateCheckDue(0, now));
        assertFalse(PowerPolicy.isAutomaticUpdateCheckDue(now - 1_000L, now));
        assertTrue(PowerPolicy.isAutomaticUpdateCheckDue(
                now - PowerPolicy.AUTOMATIC_UPDATE_CHECK_INTERVAL_MS,
                now
        ));
        assertTrue(PowerPolicy.isAutomaticUpdateCheckDue(now + 1_000L, now));
    }

    @Test
    public void quickResumeAndProcessCacheReuseFreshData() {
        long now = 1_000_000L;
        assertFalse(PowerPolicy.isForegroundRefreshDue(now - 30_000L, now));
        assertTrue(PowerPolicy.isForegroundRefreshDue(
                now - PowerPolicy.RESUME_REFRESH_FRESHNESS_MS,
                now
        ));
        assertTrue(PowerPolicy.isBackgroundCoreReuseFresh(now - 30_000L, now));
        assertFalse(PowerPolicy.isBackgroundCoreReuseFresh(
                now - PowerPolicy.BACKGROUND_CORE_REUSE_MS,
                now
        ));
    }

    @Test
    public void statusNotificationPublishesOnlyForChangesOrHourlyFreshness() {
        long now = 10 * PowerPolicy.STATUS_REPUBLISH_INTERVAL_MS;
        assertTrue(PowerPolicy.shouldPublishStatus(false, true, now - 1_000L, now));
        assertTrue(PowerPolicy.shouldPublishStatus(true, false, now - 1_000L, now));
        assertFalse(PowerPolicy.shouldPublishStatus(true, true, now - 1_000L, now));
        assertTrue(PowerPolicy.shouldPublishStatus(
                true,
                true,
                now - PowerPolicy.STATUS_REPUBLISH_INTERVAL_MS,
                now
        ));
        assertTrue(PowerPolicy.shouldPublishStatus(true, true, now + 1_000L, now));
    }
}
