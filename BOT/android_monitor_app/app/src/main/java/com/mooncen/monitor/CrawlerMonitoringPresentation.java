package com.mooncen.monitor;

import java.util.Locale;

/** Locale-stable labels for crawler monitoring values. */
final class CrawlerMonitoringPresentation {
    private CrawlerMonitoringPresentation() {
    }

    static String overallStatusLabel(String status) {
        if ("healthy".equals(status)) {
            return "정상";
        }
        if ("warning".equals(status)) {
            return "주의";
        }
        if ("critical".equals(status)) {
            return "장애";
        }
        return "확인 불가";
    }

    static String latestStatusLabel(String status) {
        if ("success".equals(status)) {
            return "성공";
        }
        if ("partial_success".equals(status)) {
            return "부분 성공";
        }
        if ("failed".equals(status)) {
            return "실패";
        }
        if ("zero_provider".equals(status)) {
            return "Provider 없음";
        }
        if ("running".equals(status)) {
            return "실행 중";
        }
        return "확인 불가";
    }

    static String count(Long value, String suffix) {
        return value == null ? "확인 불가" : value + (suffix == null ? "" : suffix);
    }

    static String duration(Double seconds) {
        if (seconds == null) {
            return "확인 불가";
        }
        if (seconds < 60.0) {
            return String.format(Locale.KOREA, "%.1f초", seconds);
        }
        if (seconds < 3600.0) {
            return String.format(Locale.KOREA, "%.1f분", seconds / 60.0);
        }
        return String.format(Locale.KOREA, "%.1f시간", seconds / 3600.0);
    }

    static String age(Double seconds) {
        if (seconds == null) {
            return "확인 불가";
        }
        if (seconds < 60.0) {
            return String.format(Locale.KOREA, "%.0f초", seconds);
        }
        if (seconds < 3600.0) {
            return String.format(Locale.KOREA, "%.0f분", seconds / 60.0);
        }
        if (seconds < 86_400.0) {
            return String.format(Locale.KOREA, "%.1f시간", seconds / 3600.0);
        }
        return String.format(Locale.KOREA, "%.1f일", seconds / 86_400.0);
    }

    static String load(Double value) {
        return value == null
                ? "확인 불가"
                : String.format(Locale.KOREA, "%.2f", value);
    }

    static String percentage(Double value) {
        return value == null
                ? "확인 불가"
                : String.format(Locale.KOREA, "%.1f%%", value);
    }

    static String temperature(Double value) {
        return value == null
                ? "확인 불가"
                : String.format(Locale.KOREA, "%.1f°C", value);
    }

    static String nodeRoleLabel(String role) {
        if ("runtime".equals(role)) {
            return "현재 실행";
        }
        if ("target".equals(role)) {
            return "목표 워커";
        }
        if ("control".equals(role)) {
            return "중앙 제어";
        }
        return "노드";
    }

    static String nodeStatusLabel(CrawlerMonitoringSnapshot.Node node) {
        if (node == null || !node.valid || !node.available || "unknown".equals(node.status)) {
            return "확인 불가";
        }
        return "up".equals(node.status) ? "온라인" : "오프라인";
    }

    static String nodeTemperatureLabel(CrawlerMonitoringSnapshot.Node node) {
        if (node == null
                || !node.valid
                || !node.available
                || !"up".equals(node.status)
                || !node.temperatureAvailable
                || node.temperatureCelsius == null) {
            return "확인 불가";
        }
        return temperature(node.temperatureCelsius);
    }
}
