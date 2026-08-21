package com.mooncen.monitor;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.json.JSONObject;
import org.junit.Test;

public class CrawlerTopologyPlacementTest {
    @Test
    public void legacyCutoverKeepsCurrentTargetAndControlDistinct() throws Exception {
        CrawlerTopologyPlacement placement = CrawlerTopologyPlacement.parse(
                topology(
                        "cloud",
                        "gen1crawler",
                        "gen1db",
                        "legacy",
                        "cutover_pending",
                        true
                ),
                "cloud"
        );

        assertTrue(placement.valid);
        assertEquals("cloud", placement.runtimeNode);
        assertEquals("gen1crawler", placement.targetNode);
        assertEquals("gen1db", placement.controlNode);
        assertEquals("legacy", placement.mode);
        assertEquals(
                CrawlerTopologyPlacement.Transition.CUTOVER_PENDING,
                placement.transition
        );
        assertEquals(Boolean.TRUE, placement.runtimeDrift);
    }

    @Test
    public void distributedRuntimeMustMatchTargetWithoutDrift() throws Exception {
        CrawlerTopologyPlacement placement = CrawlerTopologyPlacement.parse(
                topology(
                        "gen1crawler",
                        "gen1crawler",
                        "gen1db",
                        "distributed",
                        "target_runtime",
                        false
                ),
                "gen1crawler"
        );

        assertTrue(placement.valid);
        assertEquals(
                CrawlerTopologyPlacement.Transition.TARGET_RUNTIME,
                placement.transition
        );
        assertEquals(Boolean.FALSE, placement.runtimeDrift);
    }

    @Test
    public void missingRequiredPlacementFieldsFailClosed() throws Exception {
        String[] requiredFields = {
                "crawler_runtime_node",
                "crawler_target_node",
                "crawler_control_node",
                "crawler_mode",
                "crawler_transition_state",
                "crawler_runtime_drift",
                "service_nodes"
        };
        for (String field : requiredFields) {
            JSONObject topology = topology(
                    "cloud",
                    "gen1crawler",
                    "gen1db",
                    "legacy",
                    "cutover_pending",
                    true
            );
            topology.remove(field);

            assertUnknown(CrawlerTopologyPlacement.parse(topology, "cloud"));
        }
        assertUnknown(CrawlerTopologyPlacement.parse(null, "cloud"));
        assertUnknown(CrawlerTopologyPlacement.parse(
                topology(
                        "cloud",
                        "gen1crawler",
                        "gen1db",
                        "legacy",
                        "cutover_pending",
                        true
                ),
                ""
        ));
    }

    @Test
    public void unknownOrContradictoryWireStateFailsClosed() throws Exception {
        assertUnknown(CrawlerTopologyPlacement.parse(
                topology(
                        "cloud",
                        "gen1crawler",
                        "gen1db",
                        "legacy",
                        "unknown",
                        true
                ),
                "cloud"
        ));
        assertUnknown(CrawlerTopologyPlacement.parse(
                topology(
                        "cloud",
                        "gen1crawler",
                        "gen1db",
                        "legacy",
                        "target_runtime",
                        true
                ),
                "cloud"
        ));
        assertUnknown(CrawlerTopologyPlacement.parse(
                topology(
                        "gen1crawler",
                        "gen1crawler",
                        "gen1db",
                        "legacy",
                        "cutover_pending",
                        false
                ),
                "gen1crawler"
        ));
        assertUnknown(CrawlerTopologyPlacement.parse(
                topology(
                        "cloud",
                        "gen1crawler",
                        "gen1db",
                        "distributed",
                        "cutover_pending",
                        true
                ),
                "cloud"
        ));
    }

    @Test
    public void checkedAndCompatibilityRuntimeMustMatchExplicitRuntime() throws Exception {
        JSONObject topology = topology(
                "cloud",
                "gen1crawler",
                "gen1db",
                "legacy",
                "cutover_pending",
                true
        );
        assertUnknown(CrawlerTopologyPlacement.parse(topology, "gen1crawler"));

        topology.getJSONObject("service_nodes").put("crawler", "gen1crawler");
        assertUnknown(CrawlerTopologyPlacement.parse(topology, "cloud"));
    }

    @Test
    public void malformedTypesAndNodeNamesFailClosed() throws Exception {
        JSONObject wrongDriftType = topology(
                "cloud",
                "gen1crawler",
                "gen1db",
                "legacy",
                "cutover_pending",
                true
        );
        wrongDriftType.put("crawler_runtime_drift", "true");
        assertUnknown(CrawlerTopologyPlacement.parse(wrongDriftType, "cloud"));

        JSONObject invalidNode = topology(
                "Cloud",
                "gen1crawler",
                "gen1db",
                "legacy",
                "cutover_pending",
                true
        );
        assertUnknown(CrawlerTopologyPlacement.parse(invalidNode, "Cloud"));
    }

    private static JSONObject topology(
            String runtime,
            String target,
            String control,
            String mode,
            String transition,
            boolean drift
    ) throws Exception {
        return new JSONObject()
                .put("crawler_runtime_node", runtime)
                .put("crawler_target_node", target)
                .put("crawler_control_node", control)
                .put("crawler_mode", mode)
                .put("crawler_transition_state", transition)
                .put("crawler_runtime_drift", drift)
                .put("service_nodes", new JSONObject().put("crawler", runtime));
    }

    private static void assertUnknown(CrawlerTopologyPlacement placement) {
        assertFalse(placement.valid);
        assertEquals("", placement.runtimeNode);
        assertEquals("", placement.targetNode);
        assertEquals("", placement.controlNode);
        assertEquals("", placement.mode);
        assertEquals(CrawlerTopologyPlacement.Transition.UNKNOWN, placement.transition);
        assertNull(placement.runtimeDrift);
    }
}
