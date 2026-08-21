package com.mooncen.monitor;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/** Parsed, validated view of the lightweight core monitoring contract. */
final class CoreStatusSnapshot {
    static final String DATABASE = "database";
    static final String FRONTEND = "frontend";
    static final String BACKEND = "backend";
    static final String CRAWLER = "crawler";

    static final List<String> SERVICE_ORDER = Collections.unmodifiableList(
            Arrays.asList(DATABASE, FRONTEND, BACKEND, CRAWLER)
    );

    final String generatedAt;
    final Topology topology;
    final Primary primary;
    final List<Service> services;
    private final Map<String, Service> servicesByKey;

    private CoreStatusSnapshot(
            String generatedAt,
            Topology topology,
            Primary primary,
            List<Service> services,
            Map<String, Service> servicesByKey
    ) {
        this.generatedAt = clean(generatedAt);
        this.topology = topology;
        this.primary = primary;
        this.services = Collections.unmodifiableList(new ArrayList<>(services));
        this.servicesByKey = Collections.unmodifiableMap(new LinkedHashMap<>(servicesByKey));
    }

    static CoreStatusSnapshot parse(JSONObject data) {
        if (data == null) {
            throw invalid("응답이 없습니다.");
        }

        String generatedAt = clean(data.optString("generated_at", ""));
        if (generatedAt.isEmpty()) {
            throw invalid("generated_at 값이 없습니다.");
        }

        JSONObject topologyJson = data.optJSONObject("topology");
        JSONObject primaryJson = data.optJSONObject("primary");
        JSONArray serviceRows = data.optJSONArray("core_services");
        if (topologyJson == null || primaryJson == null || serviceRows == null) {
            throw invalid("topology, primary 또는 core_services가 없습니다.");
        }

        JSONObject serviceNodesJson = topologyJson.optJSONObject("service_nodes");
        if (serviceNodesJson == null) {
            throw invalid("topology.service_nodes가 없습니다.");
        }
        Map<String, String> serviceNodes = new LinkedHashMap<>();
        for (String key : SERVICE_ORDER) {
            String node = clean(serviceNodesJson.optString(key, ""));
            if (node.isEmpty()) {
                throw invalid("topology.service_nodes." + key + " 값이 없습니다.");
            }
            serviceNodes.put(key, node);
        }
        String activeNode = clean(topologyJson.optString("active_node", ""));
        if (activeNode.isEmpty()) {
            throw invalid("topology.active_node 값이 없습니다.");
        }
        Topology topology = new Topology(
                topologyJson.optString("environment", ""),
                activeNode,
                serviceNodes
        );

        String expectedNode = clean(primaryJson.optString("expected_node", activeNode));
        Primary primary = new Primary(
                nullableString(primaryJson, "node"),
                expectedNode.isEmpty() ? activeNode : expectedNode,
                State.fromApi(primaryJson.optString("status", "unknown")),
                optionalBoolean(primaryJson, "ok"),
                optionalBoolean(primaryJson, "role_ok"),
                optionalBoolean(primaryJson, "database_writable"),
                optionalBoolean(primaryJson, "matches_topology"),
                stringArray(primaryJson.optJSONArray("candidates"))
        );

        List<Service> parsedServices = new ArrayList<>();
        for (int index = 0; index < serviceRows.length(); index++) {
            JSONObject row = serviceRows.optJSONObject(index);
            if (row == null) {
                throw invalid("core_services 항목 형식이 올바르지 않습니다.");
            }
            String key = clean(row.optString("service", "")).toLowerCase(Locale.ROOT);
            if (!SERVICE_ORDER.contains(key)) {
                throw invalid("알 수 없는 core service가 있습니다: " + (key.isEmpty() ? "-" : key));
            }
            String primaryNode = clean(row.optString("primary_node", serviceNodes.get(key)));
            if (primaryNode.isEmpty()) {
                primaryNode = serviceNodes.get(key);
            }
            parsedServices.add(new Service(
                    key,
                    primaryNode,
                    stringArray(row.optJSONArray("active_nodes")),
                    optionalBoolean(row, "runtime_ok"),
                    optionalBoolean(row, "functional_ok"),
                    optionalBoolean(row, "ok"),
                    State.fromApi(row.optString("status", "unknown")),
                    row.optString("detail", ""),
                    row.optString("checked_at", "")
            ));
        }
        return create(generatedAt, topology, primary, parsedServices);
    }

    static CoreStatusSnapshot create(
            String generatedAt,
            Topology topology,
            Primary primary,
            List<Service> rows
    ) {
        if (topology == null || primary == null || rows == null) {
            throw invalid("core 상태 구성 요소가 없습니다.");
        }
        Map<String, Service> byKey = new LinkedHashMap<>();
        for (Service row : rows) {
            if (row == null || !SERVICE_ORDER.contains(row.key)) {
                throw invalid("core service 항목이 올바르지 않습니다.");
            }
            if (byKey.put(row.key, row) != null) {
                throw invalid("core service가 중복되었습니다: " + row.key);
            }
        }
        if (!byKey.keySet().equals(new LinkedHashSet<>(SERVICE_ORDER))) {
            throw invalid("DB, FRONT, BACKEND, CRAWLER 상태가 모두 필요합니다.");
        }

        List<Service> ordered = new ArrayList<>();
        for (String key : SERVICE_ORDER) {
            ordered.add(byKey.get(key));
        }
        return new CoreStatusSnapshot(generatedAt, topology, primary, ordered, byKey);
    }

    Service service(String key) {
        return servicesByKey.get(key);
    }

    State overallState() {
        boolean hasWarning = primary.state == State.WARNING;
        boolean hasUnknown = primary.state == State.UNKNOWN;
        if (primary.state == State.CRITICAL) {
            return State.CRITICAL;
        }
        for (Service service : services) {
            if (service.state == State.CRITICAL) {
                return State.CRITICAL;
            }
            hasWarning |= service.state == State.WARNING;
            hasUnknown |= service.state == State.UNKNOWN;
        }
        if (hasWarning) {
            return State.WARNING;
        }
        return hasUnknown ? State.UNKNOWN : State.HEALTHY;
    }

    int healthyServiceCount() {
        int result = 0;
        for (Service service : services) {
            if (service.state == State.HEALTHY) {
                result++;
            }
        }
        return result;
    }

    String observedPrimaryNode() {
        return clean(primary.node);
    }

    String expectedPrimaryNode() {
        String expected = clean(primary.expectedNode);
        return expected.isEmpty() ? clean(topology.activeNode) : expected;
    }

    static String serviceLabel(String key) {
        if (DATABASE.equals(key)) {
            return "DB";
        }
        if (FRONTEND.equals(key)) {
            return "FRONT";
        }
        if (BACKEND.equals(key)) {
            return "BACKEND";
        }
        if (CRAWLER.equals(key)) {
            return "CRAWLER";
        }
        return clean(key).isEmpty() ? "-" : clean(key).toUpperCase(Locale.ROOT);
    }

    private static Boolean optionalBoolean(JSONObject value, String key) {
        if (!value.has(key) || value.isNull(key)) {
            return null;
        }
        Object raw = value.opt(key);
        if (!(raw instanceof Boolean)) {
            throw invalid(key + " 값은 boolean 또는 null이어야 합니다.");
        }
        return (Boolean) raw;
    }

    private static String nullableString(JSONObject value, String key) {
        if (!value.has(key) || value.isNull(key)) {
            return "";
        }
        return clean(value.optString(key, ""));
    }

    private static List<String> stringArray(JSONArray values) {
        if (values == null) {
            return Collections.emptyList();
        }
        Set<String> result = new LinkedHashSet<>();
        for (int index = 0; index < values.length(); index++) {
            String item = clean(values.optString(index, ""));
            if (!item.isEmpty()) {
                result.add(item);
            }
        }
        return Collections.unmodifiableList(new ArrayList<>(result));
    }

    private static String clean(String value) {
        return value == null ? "" : value.trim();
    }

    private static IllegalStateException invalid(String detail) {
        return new IllegalStateException("핵심 상태 API 응답 형식이 올바르지 않습니다. " + detail);
    }

    enum State {
        HEALTHY,
        WARNING,
        CRITICAL,
        UNKNOWN;

        static State fromApi(String value) {
            String normalized = clean(value).toLowerCase(Locale.ROOT);
            if ("healthy".equals(normalized)) {
                return HEALTHY;
            }
            if ("warning".equals(normalized)) {
                return WARNING;
            }
            if ("critical".equals(normalized)) {
                return CRITICAL;
            }
            return UNKNOWN;
        }
    }

    static final class Topology {
        final String environment;
        final String activeNode;
        final Map<String, String> serviceNodes;

        Topology(String environment, String activeNode, Map<String, String> serviceNodes) {
            this.environment = clean(environment);
            this.activeNode = clean(activeNode);
            this.serviceNodes = Collections.unmodifiableMap(new LinkedHashMap<>(serviceNodes));
        }
    }

    static final class Primary {
        final String node;
        final String expectedNode;
        final State state;
        final Boolean ok;
        final Boolean roleOk;
        final Boolean databaseWritable;
        final Boolean matchesTopology;
        final List<String> candidates;

        Primary(
                String node,
                String expectedNode,
                State state,
                Boolean ok,
                Boolean roleOk,
                Boolean databaseWritable,
                Boolean matchesTopology,
                List<String> candidates
        ) {
            this.node = clean(node);
            this.expectedNode = clean(expectedNode);
            this.state = state == null ? State.UNKNOWN : state;
            this.ok = ok;
            this.roleOk = roleOk;
            this.databaseWritable = databaseWritable;
            this.matchesTopology = matchesTopology;
            this.candidates = immutableStrings(candidates);
        }
    }

    static final class Service {
        final String key;
        final String primaryNode;
        final List<String> activeNodes;
        final Boolean runtimeOk;
        final Boolean functionalOk;
        final Boolean ok;
        final State state;
        final String detail;
        final String checkedAt;

        Service(
                String key,
                String primaryNode,
                List<String> activeNodes,
                Boolean runtimeOk,
                Boolean functionalOk,
                Boolean ok,
                State state,
                String detail,
                String checkedAt
        ) {
            this.key = clean(key).toLowerCase(Locale.ROOT);
            this.primaryNode = clean(primaryNode);
            this.activeNodes = immutableStrings(activeNodes);
            this.runtimeOk = runtimeOk;
            this.functionalOk = functionalOk;
            this.ok = ok;
            this.state = state == null ? State.UNKNOWN : state;
            this.detail = clean(detail);
            this.checkedAt = clean(checkedAt);
        }
    }

    private static List<String> immutableStrings(List<String> values) {
        if (values == null || values.isEmpty()) {
            return Collections.emptyList();
        }
        Set<String> cleaned = new LinkedHashSet<>();
        for (String value : values) {
            String item = clean(value);
            if (!item.isEmpty()) {
                cleaned.add(item);
            }
        }
        return Collections.unmodifiableList(new ArrayList<>(cleaned));
    }
}
