package com.mooncen.monitor;

import java.util.Locale;

final class MonitorPresentation {
    static final int LEVEL_HEALTHY = 0;
    static final int LEVEL_WARNING = 1;
    static final int LEVEL_CRITICAL = 2;
    static final int LEVEL_UNKNOWN = 3;

    private MonitorPresentation() {
    }

    static int backupLevel(
            boolean oneShot,
            boolean active,
            boolean freshKnown,
            boolean fresh,
            boolean hasLastSuccess,
            String health
    ) {
        String normalizedHealth = normalize(health);
        if (isCriticalHealth(normalizedHealth)) {
            return LEVEL_CRITICAL;
        }
        if (freshKnown && !fresh) {
            return LEVEL_WARNING;
        }
        if (isWarningHealth(normalizedHealth)) {
            return LEVEL_WARNING;
        }
        if ((freshKnown && fresh) || isHealthyHealth(normalizedHealth)) {
            return LEVEL_HEALTHY;
        }
        if (!oneShot && active) {
            return LEVEL_HEALTHY;
        }
        return hasLastSuccess ? LEVEL_WARNING : LEVEL_UNKNOWN;
    }

    static String backupBadge(
            boolean oneShot,
            boolean active,
            boolean freshKnown,
            boolean fresh,
            boolean hasLastSuccess,
            String health
    ) {
        int level = backupLevel(
                oneShot,
                active,
                freshKnown,
                fresh,
                hasLastSuccess,
                health
        );
        if (level == LEVEL_CRITICAL) {
            return "백업 오류";
        }
        if (freshKnown) {
            return fresh ? "최신" : "갱신 필요";
        }
        if (isHealthyHealth(normalize(health))) {
            return "정상";
        }
        if (isWarningHealth(normalize(health))) {
            return "주의";
        }
        if (!oneShot && active) {
            return "실행 중";
        }
        return hasLastSuccess ? "성공 기록" : "확인 필요";
    }

    static int peerLevel(boolean online, boolean active) {
        return online ? LEVEL_HEALTHY : LEVEL_CRITICAL;
    }

    static String peerBadge(boolean online, boolean active) {
        return online ? "온라인" : "오프라인";
    }

    static String connectionLabel(String connection) {
        String normalized = normalize(connection);
        if ("direct".equals(normalized) || normalized.startsWith("direct-")) {
            return "직접 연결";
        }
        if ("relay".equals(normalized)
                || "derp".equals(normalized)
                || normalized.startsWith("relay-")
                || normalized.startsWith("derp-")) {
            return "릴레이";
        }
        if ("none".equals(normalized) || normalized.isEmpty() || "unknown".equals(normalized)) {
            return "경로 확인";
        }
        return connection;
    }

    static String tailscaleDisplayName(String dnsName, String rawName, String fallback) {
        String normalizedDnsName = dnsName == null ? "" : dnsName.trim();
        while (normalizedDnsName.endsWith(".")) {
            normalizedDnsName = normalizedDnsName.substring(0, normalizedDnsName.length() - 1);
        }
        if (!normalizedDnsName.isEmpty()) {
            String shortAlias = normalizedDnsName.split("\\.", 2)[0].trim();
            if (!shortAlias.isEmpty()) {
                return shortAlias;
            }
        }
        if (rawName != null && !rawName.trim().isEmpty()) {
            return rawName.trim();
        }
        return fallback == null || fallback.trim().isEmpty()
                ? "이름 없는 피어"
                : fallback.trim();
    }

    static String tailscaleErrorMessage(String errorCode) {
        String normalized = normalize(errorCode);
        if ("snapshot_missing".equals(normalized)) {
            return "Tailscale 상태 스냅샷이 아직 생성되지 않았습니다.";
        }
        if ("snapshot_unreadable".equals(normalized)) {
            return "Tailscale 상태 스냅샷을 읽을 수 없습니다.";
        }
        if ("snapshot_invalid".equals(normalized)) {
            return "Tailscale 상태 스냅샷 형식이 올바르지 않습니다.";
        }
        if ("snapshot_stale".equals(normalized) || "stale".equals(normalized)) {
            return "Tailscale 상태 정보가 오래되었습니다. 수집 상태를 확인하세요.";
        }
        return "Tailscale 상태를 가져올 수 없습니다.";
    }

    static String roleLabel(String role) {
        String normalized = normalize(role);
        if ("ai".equals(normalized)
                || "ai-server".equals(normalized)
                || "ai_inference".equals(normalized)) {
            return "AI";
        }
        if ("monitoring".equals(normalized)) {
            return "모니터링";
        }
        if ("linux".equals(normalized)) {
            return "Linux";
        }
        if ("windows".equals(normalized)) {
            return "Windows";
        }
        if ("synology".equals(normalized)) {
            return "Synology";
        }
        if ("db".equals(normalized)) {
            return "DB";
        }
        if ("web".equals(normalized)) {
            return "WEB";
        }
        if ("crawler".equals(normalized)) {
            return "크롤러";
        }
        if ("legacy".equals(normalized)) {
            return "이전 서버";
        }
        return role == null || role.trim().isEmpty() ? "-" : role.trim();
    }

    private static boolean isHealthyHealth(String health) {
        return "healthy".equals(health)
                || "ok".equals(health)
                || "success".equals(health)
                || "good".equals(health);
    }

    private static boolean isWarningHealth(String health) {
        return "warning".equals(health)
                || "warn".equals(health)
                || "degraded".equals(health)
                || "stale".equals(health);
    }

    private static boolean isCriticalHealth(String health) {
        return "critical".equals(health)
                || "error".equals(health)
                || "failed".equals(health)
                || "failure".equals(health)
                || "unhealthy".equals(health)
                || "missing".equals(health);
    }

    private static String normalize(String value) {
        return value == null ? "" : value.trim().toLowerCase(Locale.ROOT);
    }
}
