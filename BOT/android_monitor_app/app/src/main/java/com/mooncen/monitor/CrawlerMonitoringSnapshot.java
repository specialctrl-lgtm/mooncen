package com.mooncen.monitor;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/** Strict, section-wise parser for crawler monitoring schema version 1. */
final class CrawlerMonitoringSnapshot {
    static final int SCHEMA_VERSION = 1;
    private static final long MAX_COUNT = 1_000_000_000_000L;
    private static final double MAX_DURATION_SECONDS = 2_678_400.0;
    private static final int MAX_PROVIDER_ITEMS = 20;
    private static final int MAX_ERROR_ITEMS = 8;
    private static final int MAX_REASON_ITEMS = 8;
    private static final int MAX_QUALITY_ISSUE_STATUSES = 100;
    private static final String[] QUALITY_COUNT_FIELDS = new String[]{
            "active_courses",
            "missing_required",
            "invalid_dates",
            "invalid_prices",
            "missing_address",
            "missing_coordinates",
            "incomplete_location",
            "out_of_korea",
            "duplicate_urls",
            "blocked_sync"
    };

    final boolean contractValid;
    final boolean available;
    final boolean complete;
    final boolean partial;
    final String generatedAt;
    final String status;
    final CrawlerTopologyPlacement topology;
    final Latest latest;
    final Summary24h summary24h;
    final Providers providers;
    final Quality quality;
    final List<Node> nodes;
    final List<SectionError> errors;

    private CrawlerMonitoringSnapshot(
            boolean contractValid,
            boolean available,
            boolean complete,
            boolean partial,
            String generatedAt,
            String status,
            CrawlerTopologyPlacement topology,
            Latest latest,
            Summary24h summary24h,
            Providers providers,
            Quality quality,
            List<Node> nodes,
            List<SectionError> errors
    ) {
        this.contractValid = contractValid;
        this.available = available;
        this.complete = complete;
        this.partial = partial;
        this.generatedAt = clean(generatedAt);
        this.status = clean(status);
        this.topology = topology;
        this.latest = latest;
        this.summary24h = summary24h;
        this.providers = providers;
        this.quality = quality;
        this.nodes = Collections.unmodifiableList(new ArrayList<>(nodes));
        this.errors = Collections.unmodifiableList(new ArrayList<>(errors));
    }

    static CrawlerMonitoringSnapshot parse(JSONObject data) {
        JSONObject root = data == null ? new JSONObject() : data;
        Long schemaVersion = strictNonNegativeLong(root, "schema_version");
        String generatedAt = strictString(root, "generated_at", 128);
        Boolean rootAvailable = strictBoolean(root, "available");
        Boolean rootComplete = strictBoolean(root, "complete");
        Boolean rootPartial = strictBoolean(root, "partial");
        String rootStatus = normalizedStatus(root, "status");
        boolean contractValid = schemaVersion != null
                && schemaVersion == SCHEMA_VERSION
                && !generatedAt.isEmpty()
                && rootAvailable != null
                && rootComplete != null
                && rootPartial != null
                && Boolean.TRUE.equals(rootAvailable)
                == (Boolean.TRUE.equals(rootComplete) || Boolean.TRUE.equals(rootPartial))
                && !(Boolean.TRUE.equals(rootComplete) && Boolean.TRUE.equals(rootPartial))
                && isOverallStatus(rootStatus);

        JSONObject topologyJson = root.optJSONObject("topology");
        String explicitRuntime = strictString(topologyJson, "crawler_runtime_node", 64);
        CrawlerTopologyPlacement topology = CrawlerTopologyPlacement.parse(
                topologyJson,
                explicitRuntime
        );
        return new CrawlerMonitoringSnapshot(
                contractValid,
                Boolean.TRUE.equals(rootAvailable),
                Boolean.TRUE.equals(rootComplete),
                Boolean.TRUE.equals(rootPartial),
                generatedAt,
                isOverallStatus(rootStatus) ? rootStatus : "unknown",
                topology,
                parseLatest(root.optJSONObject("latest")),
                parseSummary(root.optJSONObject("summary_24h")),
                parseProviders(root.optJSONObject("providers")),
                parseQuality(root.optJSONObject("quality"), root.has("quality")),
                parseNodes(root.optJSONArray("nodes"), topology),
                parseErrors(root.optJSONArray("errors"))
        );
    }

    Node node(String role) {
        for (Node node : nodes) {
            if (node.role.equals(role)) {
                return node;
            }
        }
        return Node.unavailable(role);
    }

    private static Latest parseLatest(JSONObject value) {
        if (value == null) {
            return Latest.unavailable();
        }
        Boolean available = strictBoolean(value, "available");
        String source = strictString(value, "source", 128);
        String status = normalizedStatus(value, "status");
        Boolean running = strictBoolean(value, "running");
        boolean validStatus = isLatestStatus(status);
        boolean sectionAvailable = Boolean.TRUE.equals(available)
                && !source.isEmpty()
                && validStatus
                && running != null
                && ("running".equals(status) == running);
        return new Latest(
                sectionAvailable,
                source,
                validStatus ? status : "unknown",
                Boolean.TRUE.equals(running),
                optionalString(value, "started_at", 128),
                optionalString(value, "completed_at", 128),
                optionalString(value, "last_success_at", 128),
                optionalFiniteNumber(value, "last_success_age_seconds", 0.0, 604_800.0),
                optionalDuration(value, "duration_seconds"),
                optionalCount(value, "providers_requested"),
                optionalCount(value, "providers_succeeded"),
                optionalCount(value, "providers_failed"),
                optionalCount(value, "collected_count"),
                optionalCount(value, "new_count"),
                optionalCount(value, "updated_count"),
                optionalCount(value, "skipped_count")
        );
    }

    private static Summary24h parseSummary(JSONObject value) {
        if (value == null) {
            return Summary24h.unavailable();
        }
        Boolean available = strictBoolean(value, "available");
        Boolean hasData = strictBoolean(value, "has_data");
        String source = strictString(value, "source", 128);
        Long windowHours = strictNonNegativeLong(value, "window_hours");
        List<String> reasons = parseReasons(value.optJSONArray("reasons"));
        boolean reasonsValid = reasonsMatchAvailability(value.optJSONArray("reasons"), available);
        boolean sectionAvailable = Boolean.TRUE.equals(available)
                && hasData != null
                && !source.isEmpty()
                && windowHours != null
                && windowHours == 24L
                && reasonsValid;
        return new Summary24h(
                sectionAvailable,
                sectionAvailable && hasData,
                source,
                reasons,
                optionalCount(value, "run_count"),
                optionalCount(value, "success_count"),
                optionalCount(value, "partial_count"),
                optionalCount(value, "failure_count"),
                optionalCount(value, "in_progress_count"),
                optionalCount(value, "collected_count"),
                optionalCount(value, "processed_count"),
                optionalCount(value, "new_count"),
                optionalCount(value, "updated_count"),
                optionalCount(value, "skipped_count"),
                optionalDuration(value, "avg_duration_seconds"),
                optionalString(value, "last_run_at", 128)
        );
    }

    private static Providers parseProviders(JSONObject value) {
        if (value == null) {
            return Providers.unavailable();
        }
        Boolean available = strictBoolean(value, "available");
        Boolean hasData = strictBoolean(value, "has_data");
        Long total = strictNonNegativeLong(value, "total");
        Long limit = strictNonNegativeLong(value, "limit");
        Boolean truncated = strictBoolean(value, "truncated");
        JSONArray rows = value.optJSONArray("items");
        List<String> reasons = parseReasons(value.optJSONArray("reasons"));
        boolean reasonsValid = reasonsMatchAvailability(value.optJSONArray("reasons"), available);
        boolean metadataValid = Boolean.TRUE.equals(available)
                && hasData != null
                && total != null
                && limit != null
                && limit <= MAX_PROVIDER_ITEMS
                && truncated != null
                && rows != null
                && rows.length() <= MAX_PROVIDER_ITEMS
                && reasonsValid;
        List<Provider> parsed = new ArrayList<>();
        if (rows != null) {
            for (int index = 0; index < Math.min(rows.length(), MAX_PROVIDER_ITEMS); index++) {
                Provider provider = parseProvider(rows.optJSONObject(index));
                if (provider != null) {
                    parsed.add(provider);
                }
            }
        }
        return new Providers(
                metadataValid,
                metadataValid && hasData,
                total,
                limit,
                Boolean.TRUE.equals(truncated),
                reasons,
                parsed
        );
    }

    private static Provider parseProvider(JSONObject value) {
        String provider = strictString(value, "provider", 160);
        if (provider.isEmpty()) {
            return null;
        }
        return new Provider(
                provider,
                optionalCount(value, "run_count"),
                optionalCount(value, "success_count"),
                optionalCount(value, "partial_count"),
                optionalCount(value, "failure_count"),
                optionalCount(value, "collected_count"),
                optionalCount(value, "new_count"),
                optionalCount(value, "updated_count"),
                optionalCount(value, "failed_item_count"),
                optionalPercentage(value, "success_rate"),
                optionalString(value, "last_run_at", 128)
        );
    }

    private static Quality parseQuality(JSONObject value, boolean present) {
        if (!present) {
            return Quality.omitted();
        }
        if (value == null || value.length() != 9) {
            return Quality.invalid();
        }
        Long schemaVersion = strictNonNegativeLong(value, "schema_version");
        Boolean available = strictBoolean(value, "available");
        String generatedAt = strictString(value, "generated_at", 128);
        String reasonCode = strictCode(value, "reason_code", 64);
        String source = strictString(value, "source", 64);
        JSONObject counts = value.optJSONObject("counts");
        JSONArray issueRows = value.optJSONArray("issue_statuses");
        String latestScanAt = optionalString(value, "latest_scan_at", 128);
        String ruleSource = strictString(value, "rule_source", 160);
        if (schemaVersion == null
                || schemaVersion != SCHEMA_VERSION
                || available == null
                || counts == null
                || counts.length() != QUALITY_COUNT_FIELDS.length
                || issueRows == null
                || issueRows.length() > MAX_QUALITY_ISSUE_STATUSES) {
            return Quality.invalid();
        }
        if (!Boolean.TRUE.equals(available)) {
            boolean validUnavailable = !reasonCode.isEmpty()
                    && value.has("generated_at") && value.isNull("generated_at")
                    && value.has("source") && value.isNull("source")
                    && value.has("latest_scan_at") && value.isNull("latest_scan_at")
                    && value.has("rule_source") && value.isNull("rule_source")
                    && issueRows.length() == 0
                    && qualityCountsAreNull(counts);
            return validUnavailable
                    ? Quality.unavailable(reasonCode)
                    : Quality.invalid();
        }
        List<IssueStatus> issueStatuses = parseQualityIssueStatuses(issueRows);
        Long activeCourses = strictNonNegativeLong(counts, "active_courses");
        Long missingRequired = strictNonNegativeLong(counts, "missing_required");
        Long invalidDates = strictNonNegativeLong(counts, "invalid_dates");
        Long invalidPrices = strictNonNegativeLong(counts, "invalid_prices");
        Long missingAddress = strictNonNegativeLong(counts, "missing_address");
        Long missingCoordinates = strictNonNegativeLong(counts, "missing_coordinates");
        Long incompleteLocation = strictNonNegativeLong(counts, "incomplete_location");
        Long outOfKorea = strictNonNegativeLong(counts, "out_of_korea");
        Long duplicateUrls = strictNonNegativeLong(counts, "duplicate_urls");
        Long blockedSync = strictNonNegativeLong(counts, "blocked_sync");
        boolean validAvailable = !generatedAt.isEmpty()
                && value.has("reason_code") && value.isNull("reason_code")
                && "production_database".equals(source)
                && issueStatuses != null
                && value.has("latest_scan_at")
                && (value.isNull("latest_scan_at") || !latestScanAt.isEmpty())
                && !ruleSource.isEmpty()
                && activeCourses != null
                && missingRequired != null
                && invalidDates != null
                && invalidPrices != null
                && missingAddress != null
                && missingCoordinates != null
                && incompleteLocation != null
                && outOfKorea != null
                && duplicateUrls != null
                && blockedSync != null;
        if (!validAvailable) {
            return Quality.invalid();
        }
        return new Quality(
                true,
                true,
                true,
                generatedAt,
                "",
                source,
                activeCourses,
                missingRequired,
                invalidDates,
                invalidPrices,
                missingAddress,
                missingCoordinates,
                incompleteLocation,
                outOfKorea,
                duplicateUrls,
                blockedSync,
                issueStatuses,
                latestScanAt,
                ruleSource
        );
    }

    private static boolean qualityCountsAreNull(JSONObject counts) {
        for (String field : QUALITY_COUNT_FIELDS) {
            if (!counts.has(field) || !counts.isNull(field)) {
                return false;
            }
        }
        return true;
    }

    private static List<IssueStatus> parseQualityIssueStatuses(JSONArray rows) {
        Map<String, IssueStatus> byKey = new LinkedHashMap<>();
        for (int index = 0; index < rows.length(); index++) {
            JSONObject value = rows.optJSONObject(index);
            String status = strictQualityLabel(value, "status", 64);
            String severity = strictQualityLabel(value, "severity", 64);
            Long issueCount = strictNonNegativeLong(value, "issue_count");
            if (value == null
                    || value.length() != 3
                    || status.isEmpty()
                    || severity.isEmpty()
                    || issueCount == null) {
                return null;
            }
            String key = status + "\n" + severity;
            if (byKey.containsKey(key)) {
                return null;
            }
            byKey.put(key, new IssueStatus(status, severity, issueCount));
        }
        return new ArrayList<>(byKey.values());
    }

    private static List<Node> parseNodes(
            JSONArray rows,
            CrawlerTopologyPlacement topology
    ) {
        Map<String, Node> byRole = new LinkedHashMap<>();
        if (rows == null || rows.length() != 3 || topology == null || !topology.valid) {
            return unavailableNodes();
        }
        for (int index = 0; index < rows.length(); index++) {
            JSONObject value = rows.optJSONObject(index);
            String role = strictString(value, "role", 32).toLowerCase(Locale.ROOT);
            if (!isNodeRole(role) || byRole.containsKey(role)) {
                return unavailableNodes();
            }
            byRole.put(role, parseNode(value, role, topologyNode(topology, role)));
        }
        if (byRole.size() != 3) {
            return unavailableNodes();
        }
        List<Node> result = new ArrayList<>();
        for (String role : new String[]{"runtime", "target", "control"}) {
            Node node = byRole.get(role);
            result.add(node == null ? Node.unavailable(role) : node);
        }
        return result;
    }

    private static List<Node> unavailableNodes() {
        List<Node> result = new ArrayList<>();
        for (String role : new String[]{"runtime", "target", "control"}) {
            result.add(Node.unavailable(role));
        }
        return result;
    }

    private static Node parseNode(JSONObject value, String role, String expectedNode) {
        String node = strictNode(value, "node");
        Boolean available = strictBoolean(value, "available");
        String status = strictString(value, "status", 16).toLowerCase(Locale.ROOT);
        Boolean temperatureAvailable = strictBoolean(value, "temperature_available");
        Double temperature = optionalTemperature(value, "temp_celsius");
        Double cpuPercent = optionalPercentage(value, "cpu_percent");
        Double memoryPercent = optionalPercentage(value, "memory_percent");
        Double load1m = optionalFiniteNumber(value, "load_1m", 0.0, 100_000.0);
        Double diskPercent = optionalPercentage(value, "disk_percent");
        Long logicalCpuCount = optionalPositiveLong(value, "logical_cpu_count", 4096L);
        String error = optionalString(value, "error", 256);
        boolean valid = !node.isEmpty()
                && node.equals(expectedNode)
                && available != null
                && isNodeStatus(status)
                && temperatureAvailable != null;
        boolean nodeAvailable = valid && Boolean.TRUE.equals(available);
        boolean validTemperature = valid
                && nodeAvailable
                && "up".equals(status)
                && Boolean.TRUE.equals(temperatureAvailable)
                && temperature != null;
        return new Node(
                valid,
                node,
                role,
                nodeAvailable,
                valid ? status : "unknown",
                validTemperature ? temperature : null,
                validTemperature,
                nodeAvailable ? cpuPercent : null,
                nodeAvailable ? memoryPercent : null,
                nodeAvailable ? load1m : null,
                nodeAvailable ? diskPercent : null,
                nodeAvailable ? logicalCpuCount : null,
                error
        );
    }

    private static String topologyNode(CrawlerTopologyPlacement topology, String role) {
        if ("runtime".equals(role)) {
            return topology.runtimeNode;
        }
        if ("target".equals(role)) {
            return topology.targetNode;
        }
        if ("control".equals(role)) {
            return topology.controlNode;
        }
        return "";
    }

    private static List<String> parseReasons(JSONArray rows) {
        if (rows == null || rows.length() > MAX_REASON_ITEMS) {
            return Collections.emptyList();
        }
        List<String> result = new ArrayList<>();
        for (int index = 0; index < rows.length(); index++) {
            JSONObject value = rows.optJSONObject(index);
            String code = strictCode(value, "code", 64);
            if (value == null || value.length() != 1 || code.isEmpty()) {
                return Collections.emptyList();
            }
            result.add(code);
        }
        return result;
    }

    private static boolean reasonsMatchAvailability(JSONArray rows, Boolean available) {
        if (rows == null || rows.length() > MAX_REASON_ITEMS || available == null) {
            return false;
        }
        List<String> parsed = parseReasons(rows);
        if (parsed.size() != rows.length()) {
            return false;
        }
        return Boolean.TRUE.equals(available) ? parsed.isEmpty() : !parsed.isEmpty();
    }

    private static List<SectionError> parseErrors(JSONArray rows) {
        if (rows == null) {
            return Collections.emptyList();
        }
        List<SectionError> result = new ArrayList<>();
        for (int index = 0; index < Math.min(rows.length(), MAX_ERROR_ITEMS); index++) {
            JSONObject value = rows.optJSONObject(index);
            String section = strictCode(value, "section", 64);
            String code = strictCode(value, "code", 128);
            if (value != null && value.length() == 2 && !section.isEmpty() && !code.isEmpty()) {
                result.add(new SectionError(section, code));
            }
        }
        return result;
    }

    private static String normalizedStatus(JSONObject value, String key) {
        return strictString(value, key, 32).toLowerCase(Locale.ROOT);
    }

    private static boolean isOverallStatus(String value) {
        return "healthy".equals(value)
                || "warning".equals(value)
                || "critical".equals(value)
                || "unknown".equals(value);
    }

    private static boolean isLatestStatus(String value) {
        return "success".equals(value)
                || "partial_success".equals(value)
                || "failed".equals(value)
                || "zero_provider".equals(value)
                || "running".equals(value)
                || "unknown".equals(value);
    }

    private static boolean isNodeRole(String value) {
        return "runtime".equals(value) || "target".equals(value) || "control".equals(value);
    }

    private static boolean isNodeStatus(String value) {
        return "up".equals(value) || "down".equals(value) || "unknown".equals(value);
    }

    private static String strictNode(JSONObject value, String key) {
        String node = strictString(value, key, 64);
        if (node.isEmpty()
                || !node.equals(node.toLowerCase(Locale.ROOT))
                || !Character.isLetterOrDigit(node.charAt(0))) {
            return "";
        }
        for (int index = 0; index < node.length(); index++) {
            char character = node.charAt(index);
            if (!(character >= 'a' && character <= 'z')
                    && !(character >= '0' && character <= '9')
                    && character != '_'
                    && character != '.'
                    && character != '-') {
                return "";
            }
        }
        return node;
    }

    private static String strictCode(JSONObject value, String key, int maxLength) {
        String code = strictString(value, key, maxLength);
        if (code.isEmpty() || !Character.isLowerCase(code.charAt(0))) {
            return "";
        }
        for (int index = 0; index < code.length(); index++) {
            char character = code.charAt(index);
            if (!(character >= 'a' && character <= 'z')
                    && !(character >= '0' && character <= '9')
                    && character != '_') {
                return "";
            }
        }
        return code;
    }

    private static String strictQualityLabel(JSONObject value, String key, int maxLength) {
        String label = strictString(value, key, maxLength);
        if (label.isEmpty() || !Character.isLowerCase(label.charAt(0))) {
            return "";
        }
        for (int index = 0; index < label.length(); index++) {
            char character = label.charAt(index);
            if (!(character >= 'a' && character <= 'z')
                    && !(character >= '0' && character <= '9')
                    && character != '_'
                    && character != '-') {
                return "";
            }
        }
        return label;
    }

    private static String strictString(JSONObject value, String key, int maxLength) {
        if (value == null || !value.has(key) || value.isNull(key)) {
            return "";
        }
        Object raw = value.opt(key);
        if (!(raw instanceof String)) {
            return "";
        }
        String result = clean((String) raw);
        return result.length() <= maxLength ? result : "";
    }

    private static String optionalString(JSONObject value, String key, int maxLength) {
        if (value == null || !value.has(key) || value.isNull(key)) {
            return "";
        }
        return strictString(value, key, maxLength);
    }

    private static Boolean strictBoolean(JSONObject value, String key) {
        if (value == null || !value.has(key) || value.isNull(key)) {
            return null;
        }
        Object raw = value.opt(key);
        return raw instanceof Boolean ? (Boolean) raw : null;
    }

    private static Long strictNonNegativeLong(JSONObject value, String key) {
        if (value == null || !value.has(key) || value.isNull(key)) {
            return null;
        }
        Object raw = value.opt(key);
        if (!(raw instanceof Number)) {
            return null;
        }
        double number = ((Number) raw).doubleValue();
        if (!Double.isFinite(number)
                || number < 0
                || number > MAX_COUNT
                || number != Math.rint(number)) {
            return null;
        }
        return (long) number;
    }

    private static Long optionalCount(JSONObject value, String key) {
        return strictNonNegativeLong(value, key);
    }

    private static Double optionalDuration(JSONObject value, String key) {
        return optionalFiniteNumber(value, key, 0.0, MAX_DURATION_SECONDS);
    }

    private static Double optionalPercentage(JSONObject value, String key) {
        return optionalFiniteNumber(value, key, 0.0, 100.0);
    }

    private static Long optionalPositiveLong(JSONObject value, String key, long maximum) {
        Long result = strictNonNegativeLong(value, key);
        return result != null && result > 0 && result <= maximum ? result : null;
    }

    private static Double optionalTemperature(JSONObject value, String key) {
        return optionalFiniteNumber(value, key, -20.0, 130.0);
    }

    private static Double optionalFiniteNumber(
            JSONObject value,
            String key,
            double minimum,
            double maximum
    ) {
        if (value == null || !value.has(key) || value.isNull(key)) {
            return null;
        }
        Object raw = value.opt(key);
        if (!(raw instanceof Number)) {
            return null;
        }
        double result = ((Number) raw).doubleValue();
        return Double.isFinite(result) && result >= minimum && result <= maximum
                ? result
                : null;
    }

    private static String clean(String value) {
        return value == null ? "" : value.trim();
    }

    static final class Latest {
        final boolean available;
        final String source;
        final String status;
        final boolean running;
        final String startedAt;
        final String completedAt;
        final String lastSuccessAt;
        final Double lastSuccessAgeSeconds;
        final Double durationSeconds;
        final Long providersRequested;
        final Long providersSucceeded;
        final Long providersFailed;
        final Long collectedCount;
        final Long newCount;
        final Long updatedCount;
        final Long skippedCount;

        Latest(
                boolean available,
                String source,
                String status,
                boolean running,
                String startedAt,
                String completedAt,
                String lastSuccessAt,
                Double lastSuccessAgeSeconds,
                Double durationSeconds,
                Long providersRequested,
                Long providersSucceeded,
                Long providersFailed,
                Long collectedCount,
                Long newCount,
                Long updatedCount,
                Long skippedCount
        ) {
            this.available = available;
            this.source = clean(source);
            this.status = clean(status);
            this.running = running;
            this.startedAt = clean(startedAt);
            this.completedAt = clean(completedAt);
            this.lastSuccessAt = clean(lastSuccessAt);
            this.lastSuccessAgeSeconds = lastSuccessAgeSeconds;
            this.durationSeconds = durationSeconds;
            this.providersRequested = providersRequested;
            this.providersSucceeded = providersSucceeded;
            this.providersFailed = providersFailed;
            this.collectedCount = collectedCount;
            this.newCount = newCount;
            this.updatedCount = updatedCount;
            this.skippedCount = skippedCount;
        }

        static Latest unavailable() {
            return new Latest(false, "", "unknown", false, "", "", "", null, null,
                    null, null, null, null, null, null, null);
        }
    }

    static final class Summary24h {
        final boolean available;
        final boolean hasData;
        final String source;
        final List<String> reasons;
        final Long runCount;
        final Long successCount;
        final Long partialCount;
        final Long failureCount;
        final Long inProgressCount;
        final Long collectedCount;
        final Long processedCount;
        final Long newCount;
        final Long updatedCount;
        final Long skippedCount;
        final Double averageDurationSeconds;
        final String lastRunAt;

        Summary24h(
                boolean available,
                boolean hasData,
                String source,
                List<String> reasons,
                Long runCount,
                Long successCount,
                Long partialCount,
                Long failureCount,
                Long inProgressCount,
                Long collectedCount,
                Long processedCount,
                Long newCount,
                Long updatedCount,
                Long skippedCount,
                Double averageDurationSeconds,
                String lastRunAt
        ) {
            this.available = available;
            this.hasData = hasData;
            this.source = clean(source);
            this.reasons = Collections.unmodifiableList(new ArrayList<>(reasons));
            this.runCount = runCount;
            this.successCount = successCount;
            this.partialCount = partialCount;
            this.failureCount = failureCount;
            this.inProgressCount = inProgressCount;
            this.collectedCount = collectedCount;
            this.processedCount = processedCount;
            this.newCount = newCount;
            this.updatedCount = updatedCount;
            this.skippedCount = skippedCount;
            this.averageDurationSeconds = averageDurationSeconds;
            this.lastRunAt = clean(lastRunAt);
        }

        static Summary24h unavailable() {
            return new Summary24h(false, false, "", Collections.emptyList(), null, null, null, null,
                    null, null, null, null, null, null, null, "");
        }
    }

    static final class Providers {
        final boolean available;
        final boolean hasData;
        final Long total;
        final Long limit;
        final boolean truncated;
        final List<String> reasons;
        final List<Provider> items;

        Providers(
                boolean available,
                boolean hasData,
                Long total,
                Long limit,
                boolean truncated,
                List<String> reasons,
                List<Provider> items
        ) {
            this.available = available;
            this.hasData = hasData;
            this.total = total;
            this.limit = limit;
            this.truncated = truncated;
            this.reasons = Collections.unmodifiableList(new ArrayList<>(reasons));
            this.items = Collections.unmodifiableList(new ArrayList<>(items));
        }

        static Providers unavailable() {
            return new Providers(
                    false,
                    false,
                    null,
                    null,
                    false,
                    Collections.emptyList(),
                    Collections.emptyList()
            );
        }
    }

    static final class Provider {
        final String provider;
        final Long runCount;
        final Long successCount;
        final Long partialCount;
        final Long failureCount;
        final Long collectedCount;
        final Long newCount;
        final Long updatedCount;
        final Long failedItemCount;
        final Double successRate;
        final String lastRunAt;

        Provider(
                String provider,
                Long runCount,
                Long successCount,
                Long partialCount,
                Long failureCount,
                Long collectedCount,
                Long newCount,
                Long updatedCount,
                Long failedItemCount,
                Double successRate,
                String lastRunAt
        ) {
            this.provider = clean(provider);
            this.runCount = runCount;
            this.successCount = successCount;
            this.partialCount = partialCount;
            this.failureCount = failureCount;
            this.collectedCount = collectedCount;
            this.newCount = newCount;
            this.updatedCount = updatedCount;
            this.failedItemCount = failedItemCount;
            this.successRate = successRate;
            this.lastRunAt = clean(lastRunAt);
        }
    }

    static final class Quality {
        final boolean present;
        final boolean contractValid;
        final boolean available;
        final String generatedAt;
        final String reasonCode;
        final String source;
        final Long activeCourses;
        final Long missingRequired;
        final Long invalidDates;
        final Long invalidPrices;
        final Long missingAddress;
        final Long missingCoordinates;
        final Long incompleteLocation;
        final Long outOfKorea;
        final Long duplicateUrls;
        final Long blockedSync;
        final List<IssueStatus> issueStatuses;
        final String latestScanAt;
        final String ruleSource;

        Quality(
                boolean present,
                boolean contractValid,
                boolean available,
                String generatedAt,
                String reasonCode,
                String source,
                Long activeCourses,
                Long missingRequired,
                Long invalidDates,
                Long invalidPrices,
                Long missingAddress,
                Long missingCoordinates,
                Long incompleteLocation,
                Long outOfKorea,
                Long duplicateUrls,
                Long blockedSync,
                List<IssueStatus> issueStatuses,
                String latestScanAt,
                String ruleSource
        ) {
            this.present = present;
            this.contractValid = contractValid;
            this.available = available;
            this.generatedAt = clean(generatedAt);
            this.reasonCode = clean(reasonCode);
            this.source = clean(source);
            this.activeCourses = activeCourses;
            this.missingRequired = missingRequired;
            this.invalidDates = invalidDates;
            this.invalidPrices = invalidPrices;
            this.missingAddress = missingAddress;
            this.missingCoordinates = missingCoordinates;
            this.incompleteLocation = incompleteLocation;
            this.outOfKorea = outOfKorea;
            this.duplicateUrls = duplicateUrls;
            this.blockedSync = blockedSync;
            this.issueStatuses = Collections.unmodifiableList(new ArrayList<>(issueStatuses));
            this.latestScanAt = clean(latestScanAt);
            this.ruleSource = clean(ruleSource);
        }

        static Quality omitted() {
            return unavailable(false, true, "quality_not_provided");
        }

        static Quality unavailable(String reasonCode) {
            return unavailable(true, true, reasonCode);
        }

        static Quality invalid() {
            return unavailable(true, false, "quality_contract_invalid");
        }

        private static Quality unavailable(
                boolean present,
                boolean contractValid,
                String reasonCode
        ) {
            return new Quality(
                    present,
                    contractValid,
                    false,
                    "",
                    reasonCode,
                    "",
                    null,
                    null,
                    null,
                    null,
                    null,
                    null,
                    null,
                    null,
                    null,
                    null,
                    Collections.emptyList(),
                    "",
                    ""
            );
        }
    }

    static final class IssueStatus {
        final String status;
        final String severity;
        final Long issueCount;

        IssueStatus(String status, String severity, Long issueCount) {
            this.status = clean(status);
            this.severity = clean(severity);
            this.issueCount = issueCount;
        }
    }

    static final class Node {
        final boolean valid;
        final String node;
        final String role;
        final boolean available;
        final String status;
        final Double temperatureCelsius;
        final boolean temperatureAvailable;
        final Double cpuPercent;
        final Double memoryPercent;
        final Double load1m;
        final Double diskPercent;
        final Long logicalCpuCount;
        final String error;

        Node(
                boolean valid,
                String node,
                String role,
                boolean available,
                String status,
                Double temperatureCelsius,
                boolean temperatureAvailable,
                Double cpuPercent,
                Double memoryPercent,
                Double load1m,
                Double diskPercent,
                Long logicalCpuCount,
                String error
        ) {
            this.valid = valid;
            this.node = clean(node);
            this.role = clean(role);
            this.available = available;
            this.status = clean(status);
            this.temperatureCelsius = temperatureCelsius;
            this.temperatureAvailable = temperatureAvailable;
            this.cpuPercent = cpuPercent;
            this.memoryPercent = memoryPercent;
            this.load1m = load1m;
            this.diskPercent = diskPercent;
            this.logicalCpuCount = logicalCpuCount;
            this.error = clean(error);
        }

        static Node unavailable(String role) {
            return new Node(
                    false,
                    "",
                    role,
                    false,
                    "unknown",
                    null,
                    false,
                    null,
                    null,
                    null,
                    null,
                    null,
                    ""
            );
        }
    }

    static final class SectionError {
        final String section;
        final String code;

        SectionError(String section, String code) {
            this.section = clean(section);
            this.code = clean(code);
        }
    }
}
