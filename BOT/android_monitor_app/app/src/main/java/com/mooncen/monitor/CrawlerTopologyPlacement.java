package com.mooncen.monitor;

import org.json.JSONObject;

import java.util.Locale;

/** Strict, fail-closed view of crawler runtime, target, and control placement. */
final class CrawlerTopologyPlacement {
    private static final int MAX_NODE_LENGTH = 64;

    enum Transition {
        CUTOVER_PENDING,
        TARGET_RUNTIME,
        UNKNOWN
    }

    final boolean valid;
    final String runtimeNode;
    final String targetNode;
    final String controlNode;
    final String mode;
    final Transition transition;
    final Boolean runtimeDrift;

    private CrawlerTopologyPlacement(
            boolean valid,
            String runtimeNode,
            String targetNode,
            String controlNode,
            String mode,
            Transition transition,
            Boolean runtimeDrift
    ) {
        this.valid = valid;
        this.runtimeNode = clean(runtimeNode);
        this.targetNode = clean(targetNode);
        this.controlNode = clean(controlNode);
        this.mode = clean(mode);
        this.transition = transition == null ? Transition.UNKNOWN : transition;
        this.runtimeDrift = runtimeDrift;
    }

    static CrawlerTopologyPlacement parse(
            JSONObject topology,
            String checkedRuntimeNode
    ) {
        if (topology == null) {
            return unknown();
        }
        String runtimeNode = strictNode(topology, "crawler_runtime_node");
        String targetNode = strictNode(topology, "crawler_target_node");
        String controlNode = strictNode(topology, "crawler_control_node");
        String mode = strictString(topology, "crawler_mode");
        String rawTransition = strictString(topology, "crawler_transition_state");
        Boolean runtimeDrift = strictBoolean(topology, "crawler_runtime_drift");
        JSONObject serviceNodes = topology.optJSONObject("service_nodes");
        String compatibilityRuntime = strictNode(serviceNodes, "crawler");
        String checkedRuntime = validNode(checkedRuntimeNode) ? clean(checkedRuntimeNode) : "";
        if (runtimeNode.isEmpty()
                || targetNode.isEmpty()
                || controlNode.isEmpty()
                || checkedRuntime.isEmpty()
                || compatibilityRuntime.isEmpty()
                || runtimeDrift == null
                || !("legacy".equals(mode) || "distributed".equals(mode))) {
            return unknown();
        }

        Transition transition;
        if ("cutover_pending".equals(rawTransition)) {
            transition = Transition.CUTOVER_PENDING;
        } else if ("target_runtime".equals(rawTransition)) {
            transition = Transition.TARGET_RUNTIME;
        } else {
            return unknown();
        }

        if (!runtimeNode.equals(compatibilityRuntime)
                || !runtimeNode.equals(checkedRuntime)) {
            return unknown();
        }
        boolean atTarget = runtimeNode.equals(targetNode);
        boolean validPending = transition == Transition.CUTOVER_PENDING
                && !atTarget
                && runtimeDrift;
        boolean validTarget = transition == Transition.TARGET_RUNTIME
                && atTarget
                && !runtimeDrift;
        if (!(validPending || validTarget)) {
            return unknown();
        }
        if ("distributed".equals(mode) && !validTarget) {
            return unknown();
        }
        return new CrawlerTopologyPlacement(
                true,
                runtimeNode,
                targetNode,
                controlNode,
                mode,
                transition,
                runtimeDrift
        );
    }

    private static CrawlerTopologyPlacement unknown() {
        return new CrawlerTopologyPlacement(
                false,
                "",
                "",
                "",
                "",
                Transition.UNKNOWN,
                null
        );
    }

    private static String strictNode(JSONObject value, String key) {
        String candidate = strictString(value, key);
        return validNode(candidate) ? candidate : "";
    }

    private static String strictString(JSONObject value, String key) {
        if (value == null || !value.has(key) || value.isNull(key)) {
            return "";
        }
        Object raw = value.opt(key);
        return raw instanceof String ? clean((String) raw) : "";
    }

    private static Boolean strictBoolean(JSONObject value, String key) {
        if (value == null || !value.has(key) || value.isNull(key)) {
            return null;
        }
        Object raw = value.opt(key);
        return raw instanceof Boolean ? (Boolean) raw : null;
    }

    private static boolean validNode(String value) {
        String normalized = clean(value);
        if (normalized.isEmpty()
                || normalized.length() > MAX_NODE_LENGTH
                || !normalized.equals(normalized.toLowerCase(Locale.ROOT))
                || !Character.isLetterOrDigit(normalized.charAt(0))) {
            return false;
        }
        for (int index = 0; index < normalized.length(); index++) {
            char character = normalized.charAt(index);
            if (!(character >= 'a' && character <= 'z')
                    && !(character >= '0' && character <= '9')
                    && character != '_'
                    && character != '.'
                    && character != '-') {
                return false;
            }
        }
        return true;
    }

    private static String clean(String value) {
        return value == null ? "" : value.trim();
    }
}
