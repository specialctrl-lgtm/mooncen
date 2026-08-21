package com.mooncen.monitor;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertSame;
import static org.junit.Assert.assertTrue;

import org.json.JSONObject;
import org.junit.After;
import org.junit.Test;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public class RecentCoreSnapshotCacheTest {
    @After
    public void clearCache() {
        RecentCoreSnapshotCache.clear();
    }

    @Test
    public void freshMatchingResultCanBeReusedForOneMinute() throws Exception {
        CoreStatusSnapshot snapshot = healthySnapshot("2026-08-08T00:00:00Z");
        JSONObject data = new JSONObject().put("generated_at", snapshot.generatedAt);
        String key = RecentCoreSnapshotCache.requestKey("https://monitor/core", "token-a");

        RecentCoreSnapshotCache.record(key, data, snapshot, 7L, 1_000L);

        RecentCoreSnapshotCache.Entry fresh = RecentCoreSnapshotCache.getFresh(key, 30_000L);
        assertSame(snapshot, fresh.snapshot);
        assertEquals(7L, fresh.observationSequence);
        assertEquals(snapshot.generatedAt, fresh.data().getString("generated_at"));
        assertEquals(7L, RecentCoreSnapshotCache.latestAcceptedObservationSequence());
        assertFalse(RecentCoreSnapshotCache.latestAcceptedObservationUnavailable());
        assertNull(RecentCoreSnapshotCache.getFresh(
                RecentCoreSnapshotCache.requestKey("https://monitor/core", "token-b"),
                30_000L
        ));
        assertNull(RecentCoreSnapshotCache.getFresh(key, 61_000L));
    }

    @Test
    public void olderObservationCannotReplaceNewerResult() throws Exception {
        String key = RecentCoreSnapshotCache.requestKey("https://monitor/core", "token");
        CoreStatusSnapshot newer = healthySnapshot("newer");
        CoreStatusSnapshot older = healthySnapshot("older");
        RecentCoreSnapshotCache.record(
                key,
                new JSONObject().put("generated_at", "newer"),
                newer,
                10L,
                2_000L
        );
        RecentCoreSnapshotCache.record(
                key,
                new JSONObject().put("generated_at", "older"),
                older,
                9L,
                3_000L
        );

        RecentCoreSnapshotCache.Entry result = RecentCoreSnapshotCache.getFresh(key, 4_000L);
        assertSame(newer, result.snapshot);
        assertEquals(10L, result.observationSequence);
        assertEquals(2_000L, result.recordedAtElapsed);
    }

    @Test
    public void unavailableObservationInvalidatesOnlyOlderOrEqualSuccess() throws Exception {
        String key = RecentCoreSnapshotCache.requestKey("https://monitor/core", "token");
        CoreStatusSnapshot first = healthySnapshot("first");
        RecentCoreSnapshotCache.record(
                key,
                new JSONObject().put("generated_at", "first"),
                first,
                7L,
                1_000L
        );

        RecentCoreSnapshotCache.invalidateThrough(6L);
        assertSame(first, RecentCoreSnapshotCache.getFresh(key, 2_000L).snapshot);
        assertEquals(7L, RecentCoreSnapshotCache.latestAcceptedObservationSequence());
        assertFalse(RecentCoreSnapshotCache.latestAcceptedObservationUnavailable());
        RecentCoreSnapshotCache.invalidateThrough(7L);
        assertNull(RecentCoreSnapshotCache.getFresh(key, 2_000L));
        assertEquals(7L, RecentCoreSnapshotCache.latestAcceptedObservationSequence());
        assertTrue(RecentCoreSnapshotCache.latestAcceptedObservationUnavailable());

        RecentCoreSnapshotCache.record(
                key,
                new JSONObject().put("generated_at", "stale"),
                healthySnapshot("stale"),
                6L,
                2_500L
        );
        assertNull(RecentCoreSnapshotCache.getFresh(key, 2_600L));
        assertTrue(RecentCoreSnapshotCache.latestAcceptedObservationUnavailable());

        CoreStatusSnapshot newer = healthySnapshot("newer");
        RecentCoreSnapshotCache.record(
                key,
                new JSONObject().put("generated_at", "newer"),
                newer,
                9L,
                3_000L
        );
        RecentCoreSnapshotCache.invalidateThrough(8L);
        assertSame(newer, RecentCoreSnapshotCache.getFresh(key, 4_000L).snapshot);
        assertEquals(9L, RecentCoreSnapshotCache.latestAcceptedObservationSequence());
        assertFalse(RecentCoreSnapshotCache.latestAcceptedObservationUnavailable());
    }

    private static CoreStatusSnapshot healthySnapshot(String generatedAt) {
        Map<String, String> nodes = new LinkedHashMap<>();
        List<CoreStatusSnapshot.Service> services = new ArrayList<>();
        for (String key : CoreStatusSnapshot.SERVICE_ORDER) {
            nodes.put(key, "cloud");
            services.add(new CoreStatusSnapshot.Service(
                    key,
                    "cloud",
                    Arrays.asList("cloud"),
                    true,
                    true,
                    true,
                    CoreStatusSnapshot.State.HEALTHY,
                    "",
                    generatedAt
            ));
        }
        return CoreStatusSnapshot.create(
                generatedAt,
                new CoreStatusSnapshot.Topology("production", "cloud", nodes),
                new CoreStatusSnapshot.Primary(
                        "cloud",
                        "cloud",
                        CoreStatusSnapshot.State.HEALTHY,
                        true,
                        true,
                        true,
                        true,
                        Arrays.asList("cloud")
                ),
                services
        );
    }
}
