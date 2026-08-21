package com.mooncen.monitor;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertThrows;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public class CoreStatusSnapshotTest {
    @Test
    public void parserAcceptsNullableEvidenceAndRestoresServiceOrder() throws Exception {
        JSONObject serviceNodes = new JSONObject();
        for (String key : CoreStatusSnapshot.SERVICE_ORDER) {
            serviceNodes.put(key, "cloud");
        }
        JSONObject topology = new JSONObject()
                .put("environment", "production")
                .put("active_node", "cloud")
                .put("service_nodes", serviceNodes);
        JSONObject primary = new JSONObject()
                .put("node", JSONObject.NULL)
                .put("expected_node", "cloud")
                .put("status", "unknown")
                .put("ok", false)
                .put("role_ok", JSONObject.NULL)
                .put("database_writable", JSONObject.NULL)
                .put("matches_topology", JSONObject.NULL)
                .put("candidates", new JSONArray());
        JSONArray rows = new JSONArray();
        for (int index = CoreStatusSnapshot.SERVICE_ORDER.size() - 1; index >= 0; index--) {
            String key = CoreStatusSnapshot.SERVICE_ORDER.get(index);
            boolean unknown = CoreStatusSnapshot.CRAWLER.equals(key);
            rows.put(new JSONObject()
                    .put("service", key)
                    .put("primary_node", "cloud")
                    .put("active_nodes", new JSONArray().put("cloud"))
                    .put("runtime_ok", true)
                    .put("functional_ok", unknown ? JSONObject.NULL : true)
                    .put("ok", !unknown)
                    .put("status", unknown ? "unknown" : "healthy")
                    .put("detail", "")
                    .put("checked_at", "2026-08-07T00:00:00Z"));
        }
        JSONObject payload = new JSONObject()
                .put("generated_at", "2026-08-07T00:00:00Z")
                .put("topology", topology)
                .put("primary", primary)
                .put("core_services", rows);

        CoreStatusSnapshot snapshot = CoreStatusSnapshot.parse(payload);

        assertEquals(CoreStatusSnapshot.DATABASE, snapshot.services.get(0).key);
        assertEquals(CoreStatusSnapshot.CRAWLER, snapshot.services.get(3).key);
        assertNull(snapshot.service(CoreStatusSnapshot.CRAWLER).functionalOk);
        assertEquals(CoreStatusSnapshot.State.UNKNOWN, snapshot.overallState());
        assertEquals("", snapshot.observedPrimaryNode());
        assertEquals("cloud", snapshot.expectedPrimaryNode());
    }

    @Test
    public void createOrdersTheFourRequiredServices() {
        List<CoreStatusSnapshot.Service> shuffled = Arrays.asList(
                service(CoreStatusSnapshot.CRAWLER, CoreStatusSnapshot.State.HEALTHY),
                service(CoreStatusSnapshot.BACKEND, CoreStatusSnapshot.State.HEALTHY),
                service(CoreStatusSnapshot.DATABASE, CoreStatusSnapshot.State.HEALTHY),
                service(CoreStatusSnapshot.FRONTEND, CoreStatusSnapshot.State.HEALTHY)
        );

        CoreStatusSnapshot snapshot = CoreStatusSnapshot.create(
                "",
                topology(),
                primary(CoreStatusSnapshot.State.HEALTHY),
                shuffled
        );

        assertEquals(CoreStatusSnapshot.DATABASE, snapshot.services.get(0).key);
        assertEquals(CoreStatusSnapshot.FRONTEND, snapshot.services.get(1).key);
        assertEquals(CoreStatusSnapshot.BACKEND, snapshot.services.get(2).key);
        assertEquals(CoreStatusSnapshot.CRAWLER, snapshot.services.get(3).key);
        assertEquals(4, snapshot.healthyServiceCount());
    }

    @Test
    public void missingOrDuplicateCoreServiceFailsClosed() {
        List<CoreStatusSnapshot.Service> missing = new ArrayList<>(Arrays.asList(
                service(CoreStatusSnapshot.DATABASE, CoreStatusSnapshot.State.HEALTHY),
                service(CoreStatusSnapshot.FRONTEND, CoreStatusSnapshot.State.HEALTHY),
                service(CoreStatusSnapshot.BACKEND, CoreStatusSnapshot.State.HEALTHY)
        ));
        assertThrows(
                IllegalStateException.class,
                () -> CoreStatusSnapshot.create(
                        "",
                        topology(),
                        primary(CoreStatusSnapshot.State.HEALTHY),
                        missing
                )
        );

        missing.add(service(CoreStatusSnapshot.BACKEND, CoreStatusSnapshot.State.CRITICAL));
        assertThrows(
                IllegalStateException.class,
                () -> CoreStatusSnapshot.create(
                        "",
                        topology(),
                        primary(CoreStatusSnapshot.State.HEALTHY),
                        missing
                )
        );
    }

    @Test
    public void overallStateKeepsWarningAndUnknownDistinctFromCritical() {
        List<CoreStatusSnapshot.Service> rows = healthyServices();
        rows.set(3, service(CoreStatusSnapshot.CRAWLER, CoreStatusSnapshot.State.WARNING));
        CoreStatusSnapshot warning = CoreStatusSnapshot.create(
                "",
                topology(),
                primary(CoreStatusSnapshot.State.HEALTHY),
                rows
        );
        assertEquals(CoreStatusSnapshot.State.WARNING, warning.overallState());

        rows.set(3, service(CoreStatusSnapshot.CRAWLER, CoreStatusSnapshot.State.UNKNOWN));
        CoreStatusSnapshot unknown = CoreStatusSnapshot.create(
                "",
                topology(),
                primary(CoreStatusSnapshot.State.HEALTHY),
                rows
        );
        assertEquals(CoreStatusSnapshot.State.UNKNOWN, unknown.overallState());

        rows.set(2, service(CoreStatusSnapshot.BACKEND, CoreStatusSnapshot.State.CRITICAL));
        CoreStatusSnapshot critical = CoreStatusSnapshot.create(
                "",
                topology(),
                primary(CoreStatusSnapshot.State.HEALTHY),
                rows
        );
        assertEquals(CoreStatusSnapshot.State.CRITICAL, critical.overallState());
    }

    @Test
    public void healthyServiceCountMatchesTheFourSummaryTiles() {
        List<CoreStatusSnapshot.Service> rows = healthyServices();
        rows.set(1, service(CoreStatusSnapshot.FRONTEND, CoreStatusSnapshot.State.WARNING));
        rows.set(3, service(CoreStatusSnapshot.CRAWLER, CoreStatusSnapshot.State.CRITICAL));

        CoreStatusSnapshot snapshot = CoreStatusSnapshot.create(
                "",
                topology(),
                primary(CoreStatusSnapshot.State.HEALTHY),
                rows
        );

        assertEquals(2, snapshot.healthyServiceCount());
        assertEquals(CoreStatusSnapshot.State.CRITICAL, snapshot.overallState());
    }

    @Test
    public void unsupportedApiStatusBecomesUnknown() {
        assertEquals(
                CoreStatusSnapshot.State.UNKNOWN,
                CoreStatusSnapshot.State.fromApi("unexpected")
        );
        assertEquals(
                CoreStatusSnapshot.State.WARNING,
                CoreStatusSnapshot.State.fromApi("WARNING")
        );
    }

    private static List<CoreStatusSnapshot.Service> healthyServices() {
        List<CoreStatusSnapshot.Service> result = new ArrayList<>();
        for (String key : CoreStatusSnapshot.SERVICE_ORDER) {
            result.add(service(key, CoreStatusSnapshot.State.HEALTHY));
        }
        return result;
    }

    private static CoreStatusSnapshot.Service service(
            String key,
            CoreStatusSnapshot.State state
    ) {
        return new CoreStatusSnapshot.Service(
                key,
                "cloud",
                Arrays.asList("cloud"),
                true,
                state == CoreStatusSnapshot.State.UNKNOWN ? null : state == CoreStatusSnapshot.State.HEALTHY,
                state == CoreStatusSnapshot.State.HEALTHY,
                state,
                "",
                ""
        );
    }

    private static CoreStatusSnapshot.Topology topology() {
        Map<String, String> nodes = new LinkedHashMap<>();
        for (String key : CoreStatusSnapshot.SERVICE_ORDER) {
            nodes.put(key, "cloud");
        }
        return new CoreStatusSnapshot.Topology("production", "cloud", nodes);
    }

    private static CoreStatusSnapshot.Primary primary(CoreStatusSnapshot.State state) {
        return new CoreStatusSnapshot.Primary(
                "cloud",
                "cloud",
                state,
                state == CoreStatusSnapshot.State.HEALTHY,
                true,
                true,
                true,
                Arrays.asList("cloud")
        );
    }
}
