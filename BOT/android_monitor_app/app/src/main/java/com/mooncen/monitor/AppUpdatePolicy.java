package com.mooncen.monitor;

import java.net.URI;
import java.net.URISyntaxException;

final class AppUpdatePolicy {
    static final String MANIFEST_URL = "https://mon.binary.kr/android/latest.json";
    static final String APK_URL = "https://mon.binary.kr/android/mooncen-monitor.apk";

    private AppUpdatePolicy() {
    }

    static boolean isUpdateAvailable(long currentVersionCode, long latestVersionCode) {
        return latestVersionCode > currentVersionCode;
    }

    static boolean isAllowedApkUrl(String rawUrl) {
        if (rawUrl == null || rawUrl.trim().isEmpty()) {
            return false;
        }
        try {
            URI uri = new URI(rawUrl.trim());
            return !uri.isOpaque()
                    && "https".equalsIgnoreCase(uri.getScheme())
                    && "mon.binary.kr".equalsIgnoreCase(uri.getHost())
                    && uri.getPort() < 0
                    && uri.getRawUserInfo() == null
                    && "/android/mooncen-monitor.apk".equals(uri.getRawPath())
                    && uri.getRawQuery() == null
                    && uri.getRawFragment() == null;
        } catch (URISyntaxException exception) {
            return false;
        }
    }

    static boolean isValidSha256(String value) {
        return value != null && value.matches("(?i)^[0-9a-f]{64}$");
    }
}
