package com.mooncen.monitor;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;

public class CrawlerQualitySnapshotTest {
    @Test
    public void legacySchemaV1WithoutQualityRemainsCompatible() throws Exception {
        CrawlerMonitoringSnapshot snapshot = CrawlerMonitoringSnapshot.parse(root());

        assertTrue(snapshot.contractValid);
        assertFalse(snapshot.quality.present);
        assertTrue(snapshot.quality.contractValid);
        assertFalse(snapshot.quality.available);
        assertEquals("quality_not_provided", snapshot.quality.reasonCode);
        assertNull(snapshot.quality.activeCourses);
    }

    @Test
    public void parsesStrictProductionQualitySummaryIncludingObservedZero() throws Exception {
        JSONObject value = root().put("quality", availableQuality());

        CrawlerMonitoringSnapshot snapshot = CrawlerMonitoringSnapshot.parse(value);

        assertTrue(snapshot.contractValid);
        assertTrue(snapshot.quality.present);
        assertTrue(snapshot.quality.contractValid);
        assertTrue(snapshot.quality.available);
        assertEquals("production_database", snapshot.quality.source);
        assertEquals(Long.valueOf(125), snapshot.quality.activeCourses);
        assertEquals(Long.valueOf(3), snapshot.quality.missingRequired);
        assertEquals(Long.valueOf(0), snapshot.quality.outOfKorea);
        assertEquals(Long.valueOf(8), snapshot.quality.blockedSync);
        assertEquals(1, snapshot.quality.issueStatuses.size());
        assertEquals("open", snapshot.quality.issueStatuses.get(0).status);
        assertEquals(Long.valueOf(9), snapshot.quality.issueStatuses.get(0).issueCount);
        assertEquals("2026-08-15T04:55:00Z", snapshot.quality.latestScanAt);
    }

    @Test
    public void malformedAvailableQualityFailsWholeOptionalSectionClosed() throws Exception {
        JSONObject quality = availableQuality();
        quality.getJSONObject("counts").put("missing_required", "0");

        CrawlerMonitoringSnapshot snapshot = CrawlerMonitoringSnapshot.parse(
                root().put("quality", quality)
        );

        assertTrue(snapshot.contractValid);
        assertTrue(snapshot.quality.present);
        assertFalse(snapshot.quality.contractValid);
        assertFalse(snapshot.quality.available);
        assertEquals("quality_contract_invalid", snapshot.quality.reasonCode);
        assertNull(snapshot.quality.activeCourses);
        assertNull(snapshot.quality.missingRequired);
        assertTrue(snapshot.quality.issueStatuses.isEmpty());
    }

    @Test
    public void unavailableQualityRequiresNullCountsInsteadOfSyntheticZeros() throws Exception {
        JSONObject unavailable = unavailableQuality("server_monitor_request_failed");
        CrawlerMonitoringSnapshot valid = CrawlerMonitoringSnapshot.parse(
                root().put("quality", unavailable)
        );

        assertTrue(valid.quality.contractValid);
        assertFalse(valid.quality.available);
        assertEquals("server_monitor_request_failed", valid.quality.reasonCode);
        assertNull(valid.quality.activeCourses);
        assertNull(valid.quality.blockedSync);

        unavailable.getJSONObject("counts").put("active_courses", 0);
        CrawlerMonitoringSnapshot syntheticZero = CrawlerMonitoringSnapshot.parse(
                root().put("quality", unavailable)
        );

        assertFalse(syntheticZero.quality.contractValid);
        assertFalse(syntheticZero.quality.available);
        assertNull(syntheticZero.quality.activeCourses);
        assertEquals("quality_contract_invalid", syntheticZero.quality.reasonCode);
    }

    @Test
    public void duplicateIssueStatusRowsInvalidateQualityOnly() throws Exception {
        JSONObject quality = availableQuality();
        quality.getJSONArray("issue_statuses").put(new JSONObject()
                .put("status", "open")
                .put("severity", "warning")
                .put("issue_count", 1));

        CrawlerMonitoringSnapshot snapshot = CrawlerMonitoringSnapshot.parse(
                root().put("quality", quality)
        );

        assertTrue(snapshot.contractValid);
        assertFalse(snapshot.quality.contractValid);
        assertFalse(snapshot.quality.available);
    }

    @Test
    public void acceptsBoundedHyphenatedIssueStatusLabels() throws Exception {
        JSONObject quality = availableQuality();
        quality.getJSONArray("issue_statuses")
                .getJSONObject(0)
                .put("status", "status-0")
                .put("severity", "medium-low");

        CrawlerMonitoringSnapshot snapshot = CrawlerMonitoringSnapshot.parse(
                root().put("quality", quality)
        );

        assertTrue(snapshot.quality.contractValid);
        assertTrue(snapshot.quality.available);
        assertEquals("status-0", snapshot.quality.issueStatuses.get(0).status);
        assertEquals("medium-low", snapshot.quality.issueStatuses.get(0).severity);
    }

    private static JSONObject root() throws Exception {
        return new JSONObject()
                .put("schema_version", 1)
                .put("generated_at", "2026-08-15T05:00:00Z")
                .put("available", false)
                .put("complete", false)
                .put("partial", false)
                .put("status", "unknown");
    }

    private static JSONObject availableQuality() throws Exception {
        return new JSONObject()
                .put("schema_version", 1)
                .put("generated_at", "2026-08-15T05:00:00Z")
                .put("available", true)
                .put("reason_code", JSONObject.NULL)
                .put("source", "production_database")
                .put("counts", qualityCounts(false))
                .put("issue_statuses", new JSONArray().put(new JSONObject()
                        .put("status", "open")
                        .put("severity", "warning")
                        .put("issue_count", 9)))
                .put("latest_scan_at", "2026-08-15T04:55:00Z")
                .put("rule_source", "production courses/service_group");
    }

    private static JSONObject unavailableQuality(String reasonCode) throws Exception {
        return new JSONObject()
                .put("schema_version", 1)
                .put("generated_at", JSONObject.NULL)
                .put("available", false)
                .put("reason_code", reasonCode)
                .put("source", JSONObject.NULL)
                .put("counts", qualityCounts(true))
                .put("issue_statuses", new JSONArray())
                .put("latest_scan_at", JSONObject.NULL)
                .put("rule_source", JSONObject.NULL);
    }

    private static JSONObject qualityCounts(boolean unavailable) throws Exception {
        return new JSONObject()
                .put("active_courses", unavailable ? JSONObject.NULL : 125)
                .put("missing_required", unavailable ? JSONObject.NULL : 3)
                .put("invalid_dates", unavailable ? JSONObject.NULL : 2)
                .put("invalid_prices", unavailable ? JSONObject.NULL : 1)
                .put("missing_address", unavailable ? JSONObject.NULL : 4)
                .put("missing_coordinates", unavailable ? JSONObject.NULL : 5)
                .put("incomplete_location", unavailable ? JSONObject.NULL : 6)
                .put("out_of_korea", unavailable ? JSONObject.NULL : 0)
                .put("duplicate_urls", unavailable ? JSONObject.NULL : 7)
                .put("blocked_sync", unavailable ? JSONObject.NULL : 8);
    }
}
