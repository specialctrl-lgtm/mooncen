package com.mooncen.monitor;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;

public class CrawlerMonitoringSnapshotTest {
    @Test
    public void parsesIndependentCrawlerMonitoringSections() throws Exception {
        CrawlerMonitoringSnapshot snapshot = CrawlerMonitoringSnapshot.parse(payload());

        assertTrue(snapshot.contractValid);
        assertTrue(snapshot.available);
        assertFalse(snapshot.complete);
        assertTrue(snapshot.partial);
        assertEquals("warning", snapshot.status);
        assertTrue(snapshot.topology.valid);
        assertEquals("cloud", snapshot.topology.runtimeNode);
        assertTrue(snapshot.latest.available);
        assertEquals("partial_success", snapshot.latest.status);
        assertEquals(Long.valueOf(124), snapshot.latest.collectedCount);
        assertEquals("2026-08-12T11:00:00Z", snapshot.latest.lastSuccessAt);
        assertEquals(Double.valueOf(3480.0), snapshot.latest.lastSuccessAgeSeconds);
        assertFalse(snapshot.latest.running);
        assertNull(snapshot.latest.durationSeconds);
        assertTrue(snapshot.summary24h.available);
        assertTrue(snapshot.summary24h.hasData);
        assertTrue(snapshot.summary24h.reasons.isEmpty());
        assertEquals(Long.valueOf(8), snapshot.summary24h.runCount);
        assertEquals(Double.valueOf(32.5), snapshot.summary24h.averageDurationSeconds);
        assertTrue(snapshot.providers.available);
        assertTrue(snapshot.providers.reasons.isEmpty());
        assertEquals(1, snapshot.providers.items.size());
        assertEquals(Double.valueOf(87.5), snapshot.providers.items.get(0).successRate);
        assertEquals(3, snapshot.nodes.size());
        assertEquals(Double.valueOf(12.3), snapshot.node("runtime").cpuPercent);
        assertEquals(Double.valueOf(45.6), snapshot.node("runtime").memoryPercent);
        assertEquals(Double.valueOf(0.42), snapshot.node("runtime").load1m);
        assertEquals(Double.valueOf(61.5), snapshot.node("runtime").diskPercent);
        assertEquals(Long.valueOf(8), snapshot.node("runtime").logicalCpuCount);
        assertFalse(snapshot.node("runtime").temperatureAvailable);
        assertNull(snapshot.node("runtime").temperatureCelsius);
        assertEquals("gen1crawler", snapshot.node("target").node);
        assertFalse(snapshot.node("target").available);
        assertNull(snapshot.node("target").cpuPercent);
        assertEquals(Double.valueOf(41.2), snapshot.node("control").temperatureCelsius);
        assertEquals(1, snapshot.errors.size());
    }

    @Test
    public void unavailableEvidenceStaysNullInsteadOfBecomingZero() throws Exception {
        JSONObject value = payload();
        value.put("available", false);
        value.put("partial", false);
        value.put("status", "unknown");
        value.put("latest", new JSONObject()
                .put("available", false)
                .put("source", "prometheus")
                .put("running", false)
                .put("status", "unknown")
                .put("started_at", JSONObject.NULL)
                .put("completed_at", JSONObject.NULL)
                .put("last_success_at", JSONObject.NULL)
                .put("last_success_age_seconds", JSONObject.NULL)
                .put("duration_seconds", JSONObject.NULL)
                .put("providers_requested", JSONObject.NULL)
                .put("providers_succeeded", JSONObject.NULL)
                .put("providers_failed", JSONObject.NULL)
                .put("collected_count", JSONObject.NULL)
                .put("new_count", JSONObject.NULL)
                .put("updated_count", JSONObject.NULL)
                .put("skipped_count", JSONObject.NULL));
        value.put("summary_24h", unavailableSummary());
        value.put("providers", new JSONObject()
                .put("available", false)
                .put("has_data", false)
                .put("reasons", new JSONArray().put(new JSONObject()
                        .put("code", "provider_statistics_unavailable")))
                .put("total", JSONObject.NULL)
                .put("limit", 20)
                .put("truncated", false)
                .put("items", new JSONArray()));

        CrawlerMonitoringSnapshot snapshot = CrawlerMonitoringSnapshot.parse(value);

        assertTrue(snapshot.contractValid);
        assertFalse(snapshot.available);
        assertFalse(snapshot.latest.available);
        assertNull(snapshot.latest.providersRequested);
        assertNull(snapshot.latest.collectedCount);
        assertNull(snapshot.latest.lastSuccessAgeSeconds);
        assertFalse(snapshot.summary24h.available);
        assertNull(snapshot.summary24h.runCount);
        assertNull(snapshot.summary24h.failureCount);
        assertFalse(snapshot.providers.available);
        assertNull(snapshot.providers.total);
        assertEquals("collection_statistics_unavailable", snapshot.summary24h.reasons.get(0));
        assertEquals("provider_statistics_unavailable", snapshot.providers.reasons.get(0));
    }

    @Test
    public void invalidRootDoesNotDiscardValidIndependentNodeEvidence() throws Exception {
        JSONObject value = payload()
                .put("schema_version", 2)
                .put("available", "true");

        CrawlerMonitoringSnapshot snapshot = CrawlerMonitoringSnapshot.parse(value);

        assertFalse(snapshot.contractValid);
        assertTrue(snapshot.topology.valid);
        assertTrue(snapshot.node("runtime").valid);
        assertEquals(Double.valueOf(12.3), snapshot.node("runtime").cpuPercent);
    }

    @Test
    public void malformedMetricsBecomeUnavailableNotSyntheticZero() throws Exception {
        JSONObject value = payload();
        value.getJSONObject("latest")
                .put("collected_count", "0")
                .put("duration_seconds", -1);
        value.getJSONObject("summary_24h")
                .put("failure_count", -1)
                .put("avg_duration_seconds", "0");
        value.getJSONObject("providers")
                .getJSONArray("items")
                .getJSONObject(0)
                .put("success_rate", 101);
        value.getJSONArray("nodes")
                .getJSONObject(0)
                .put("cpu_percent", 101)
                .put("memory_percent", "0")
                .put("temperature_available", true)
                .put("temp_celsius", 131);

        CrawlerMonitoringSnapshot snapshot = CrawlerMonitoringSnapshot.parse(value);

        assertNull(snapshot.latest.collectedCount);
        assertNull(snapshot.latest.durationSeconds);
        assertNull(snapshot.summary24h.failureCount);
        assertNull(snapshot.summary24h.averageDurationSeconds);
        assertNull(snapshot.providers.items.get(0).successRate);
        assertNull(snapshot.node("runtime").cpuPercent);
        assertNull(snapshot.node("runtime").memoryPercent);
        assertFalse(snapshot.node("runtime").temperatureAvailable);
        assertNull(snapshot.node("runtime").temperatureCelsius);
    }

    @Test
    public void missingNodeRolesFailWholeNodeSectionClosed() throws Exception {
        JSONObject value = payload();
        value.put("nodes", new JSONArray().put(value.getJSONArray("nodes").getJSONObject(0)));

        CrawlerMonitoringSnapshot snapshot = CrawlerMonitoringSnapshot.parse(value);

        assertFalse(snapshot.node("runtime").valid);
        assertFalse(snapshot.node("target").valid);
        assertFalse(snapshot.node("control").valid);
        assertEquals("target", snapshot.node("target").role);
    }

    @Test
    public void contradictoryRootAvailabilityFailsClosed() throws Exception {
        JSONObject value = payload()
                .put("available", false)
                .put("complete", false)
                .put("partial", true);

        CrawlerMonitoringSnapshot snapshot = CrawlerMonitoringSnapshot.parse(value);

        assertFalse(snapshot.contractValid);
        assertTrue(snapshot.latest.available);
        assertTrue(snapshot.node("runtime").available);
    }

    @Test
    public void latestRunningFlagMustMatchStatus() throws Exception {
        JSONObject value = payload();
        value.getJSONObject("latest").put("running", true);

        CrawlerMonitoringSnapshot snapshot = CrawlerMonitoringSnapshot.parse(value);

        assertFalse(snapshot.latest.available);
        assertEquals("partial_success", snapshot.latest.status);
    }

    @Test
    public void temperatureIsHiddenUnlessNodeIsObservedUp() throws Exception {
        JSONObject value = payload();
        value.getJSONArray("nodes").getJSONObject(2).put("status", "unknown");

        CrawlerMonitoringSnapshot snapshot = CrawlerMonitoringSnapshot.parse(value);

        assertTrue(snapshot.node("control").valid);
        assertTrue(snapshot.node("control").available);
        assertFalse(snapshot.node("control").temperatureAvailable);
        assertNull(snapshot.node("control").temperatureCelsius);
    }

    @Test
    public void nodeRolesMustMatchTopologyPlacement() throws Exception {
        JSONObject value = payload();
        value.getJSONArray("nodes").getJSONObject(0).put("node", "gen1crawler");

        CrawlerMonitoringSnapshot snapshot = CrawlerMonitoringSnapshot.parse(value);

        assertTrue(snapshot.topology.valid);
        assertFalse(snapshot.node("runtime").valid);
        assertEquals("runtime", snapshot.node("runtime").role);
        assertTrue(snapshot.node("target").valid);
        assertTrue(snapshot.node("control").valid);
    }

    @Test
    public void invalidTopologyFailsAllPlacementNodesClosed() throws Exception {
        JSONObject value = payload();
        value.getJSONObject("topology").remove("crawler_control_node");

        CrawlerMonitoringSnapshot snapshot = CrawlerMonitoringSnapshot.parse(value);

        assertFalse(snapshot.topology.valid);
        assertFalse(snapshot.node("runtime").valid);
        assertFalse(snapshot.node("target").valid);
        assertFalse(snapshot.node("control").valid);
    }

    @Test
    public void duplicateOrUnknownNodeRolesFailWholeNodeSectionClosed() throws Exception {
        JSONObject duplicate = payload();
        duplicate.getJSONArray("nodes").getJSONObject(1).put("role", "runtime");
        CrawlerMonitoringSnapshot duplicateSnapshot = CrawlerMonitoringSnapshot.parse(duplicate);

        assertFalse(duplicateSnapshot.node("runtime").valid);
        assertFalse(duplicateSnapshot.node("target").valid);
        assertFalse(duplicateSnapshot.node("control").valid);

        JSONObject unknown = payload();
        unknown.getJSONArray("nodes").getJSONObject(1).put("role", "standby");
        CrawlerMonitoringSnapshot unknownSnapshot = CrawlerMonitoringSnapshot.parse(unknown);

        assertFalse(unknownSnapshot.node("runtime").valid);
        assertFalse(unknownSnapshot.node("target").valid);
        assertFalse(unknownSnapshot.node("control").valid);
    }

    @Test
    public void extraOrMissingNodeRowsFailWholeNodeSectionClosed() throws Exception {
        JSONObject extra = payload();
        extra.getJSONArray("nodes").put(extra.getJSONArray("nodes").getJSONObject(0));
        CrawlerMonitoringSnapshot extraSnapshot = CrawlerMonitoringSnapshot.parse(extra);

        assertFalse(extraSnapshot.node("runtime").valid);
        assertFalse(extraSnapshot.node("target").valid);
        assertFalse(extraSnapshot.node("control").valid);

        JSONObject missing = payload();
        missing.getJSONArray("nodes").remove(2);
        CrawlerMonitoringSnapshot missingSnapshot = CrawlerMonitoringSnapshot.parse(missing);

        assertFalse(missingSnapshot.node("runtime").valid);
        assertFalse(missingSnapshot.node("target").valid);
        assertFalse(missingSnapshot.node("control").valid);
    }

    private static JSONObject payload() throws Exception {
        return new JSONObject()
                .put("schema_version", 1)
                .put("generated_at", "2026-08-12T12:00:00Z")
                .put("available", true)
                .put("complete", false)
                .put("partial", true)
                .put("status", "warning")
                .put("topology", topology())
                .put("latest", new JSONObject()
                        .put("available", true)
                        .put("source", "prometheus")
                        .put("running", false)
                        .put("completed_at", "2026-08-12T11:58:00Z")
                        .put("last_success_at", "2026-08-12T11:00:00Z")
                        .put("last_success_age_seconds", 3480)
                        .put("status", "partial_success")
                        .put("providers_requested", 10)
                        .put("providers_succeeded", 8)
                        .put("providers_failed", 2)
                        .put("started_at", JSONObject.NULL)
                        .put("duration_seconds", JSONObject.NULL)
                        .put("collected_count", 124)
                        .put("new_count", 30)
                        .put("updated_count", 12)
                        .put("skipped_count", 82))
                .put("summary_24h", new JSONObject()
                        .put("available", true)
                        .put("has_data", true)
                        .put("reasons", new JSONArray())
                        .put("source", "ops")
                        .put("window_hours", 24)
                        .put("run_count", 8)
                        .put("success_count", 6)
                        .put("partial_count", 1)
                        .put("failure_count", 1)
                        .put("in_progress_count", 0)
                        .put("collected_count", 900)
                        .put("processed_count", 840)
                        .put("new_count", 140)
                        .put("updated_count", 60)
                        .put("skipped_count", 640)
                        .put("avg_duration_seconds", 32.5)
                        .put("last_run_at", "2026-08-12T11:58:00Z"))
                .put("providers", new JSONObject()
                        .put("available", true)
                        .put("has_data", true)
                        .put("reasons", new JSONArray())
                        .put("total", 1)
                        .put("limit", 20)
                        .put("truncated", false)
                        .put("items", new JSONArray().put(new JSONObject()
                                .put("provider", "provider-a")
                                .put("run_count", 8)
                                .put("success_count", 7)
                                .put("partial_count", 0)
                                .put("failure_count", 1)
                                .put("collected_count", 900)
                                .put("new_count", 140)
                                .put("updated_count", 60)
                                .put("failed_item_count", 2)
                                .put("success_rate", 87.5)
                                .put("last_run_at", "2026-08-12T11:58:00Z"))))
                .put("nodes", nodes())
                .put("errors", new JSONArray().put(new JSONObject()
                        .put("section", "nodes")
                        .put("code", "temperature_unavailable")));
    }

    private static JSONObject unavailableSummary() throws Exception {
        return new JSONObject()
                .put("available", false)
                .put("has_data", false)
                .put("reasons", new JSONArray().put(new JSONObject()
                        .put("code", "collection_statistics_unavailable")))
                .put("source", "ops")
                .put("window_hours", 24)
                .put("run_count", JSONObject.NULL)
                .put("success_count", JSONObject.NULL)
                .put("partial_count", JSONObject.NULL)
                .put("failure_count", JSONObject.NULL)
                .put("in_progress_count", JSONObject.NULL)
                .put("collected_count", JSONObject.NULL)
                .put("processed_count", JSONObject.NULL)
                .put("new_count", JSONObject.NULL)
                .put("updated_count", JSONObject.NULL)
                .put("skipped_count", JSONObject.NULL)
                .put("avg_duration_seconds", JSONObject.NULL)
                .put("last_run_at", JSONObject.NULL);
    }

    private static JSONObject topology() throws Exception {
        return new JSONObject()
                .put("environment", "production")
                .put("active_node", "cloud")
                .put("crawler_runtime_node", "cloud")
                .put("crawler_target_node", "gen1crawler")
                .put("crawler_control_node", "gen1db")
                .put("crawler_mode", "legacy")
                .put("crawler_transition_state", "cutover_pending")
                .put("crawler_runtime_drift", true)
                .put("service_nodes", new JSONObject().put("crawler", "cloud"));
    }

    private static JSONArray nodes() throws Exception {
        return new JSONArray()
                .put(node(
                        "cloud", "runtime", true, "up", 12.3, 45.6,
                        0.42, 61.5, 8L, null, false, null
                ))
                .put(node(
                        "gen1crawler",
                        "target",
                        false,
                        "unknown",
                        null,
                        null,
                        null,
                        null,
                        null,
                        null,
                        false,
                        "exporter_unavailable"
                ))
                .put(node(
                        "gen1db", "control", true, "up", 5.0, 30.0,
                        0.1, 20.0, 4L, 41.2, true, null
                ));
    }

    private static JSONObject node(
            String name,
            String role,
            boolean available,
            String status,
            Double cpu,
            Double memory,
            Double load,
            Double disk,
            Long logicalCpuCount,
            Double temperature,
            boolean temperatureAvailable,
            String error
    ) throws Exception {
        return new JSONObject()
                .put("node", name)
                .put("role", role)
                .put("available", available)
                .put("status", status)
                .put("cpu_percent", cpu == null ? JSONObject.NULL : cpu)
                .put("memory_percent", memory == null ? JSONObject.NULL : memory)
                .put("load_1m", load == null ? JSONObject.NULL : load)
                .put("disk_percent", disk == null ? JSONObject.NULL : disk)
                .put(
                        "logical_cpu_count",
                        logicalCpuCount == null ? JSONObject.NULL : logicalCpuCount
                )
                .put("temp_celsius", temperature == null ? JSONObject.NULL : temperature)
                .put("temperature_available", temperatureAvailable)
                .put("error", error == null ? JSONObject.NULL : error);
    }
}
