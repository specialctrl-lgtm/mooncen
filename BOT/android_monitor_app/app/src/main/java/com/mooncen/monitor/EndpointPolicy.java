package com.mooncen.monitor;

import java.net.URI;
import java.net.URISyntaxException;
import java.util.Locale;

final class EndpointPolicy {
    private EndpointPolicy() {
    }

    static String normalizeBaseUrl(String rawValue, String defaultValue) {
        String value = rawValue == null || rawValue.trim().isEmpty()
                ? defaultValue
                : rawValue.trim();
        return value.replaceAll("/+$", "");
    }

    static String validateHttpsBaseUrl(String rawValue, String defaultValue) {
        String candidate = rawValue == null || rawValue.trim().isEmpty()
                ? defaultValue
                : rawValue.trim();
        if (candidate == null || candidate.trim().isEmpty()) {
            throw new IllegalArgumentException("API 주소를 입력하세요.");
        }
        final URI uri;
        try {
            uri = new URI(candidate);
        } catch (URISyntaxException exception) {
            throw new IllegalArgumentException("API 주소 형식을 확인하세요.");
        }

        if (!"https".equalsIgnoreCase(uri.getScheme())) {
            throw new IllegalArgumentException("API 주소는 https://로 시작해야 합니다.");
        }
        if (uri.isOpaque() || uri.getHost() == null || uri.getHost().trim().isEmpty()) {
            throw new IllegalArgumentException("API 주소의 호스트를 확인하세요.");
        }
        if (uri.getRawUserInfo() != null) {
            throw new IllegalArgumentException("API 주소에 사용자 정보를 포함할 수 없습니다.");
        }
        if (uri.getRawQuery() != null) {
            throw new IllegalArgumentException("API 주소에 쿼리 문자열을 포함할 수 없습니다.");
        }
        if (uri.getRawFragment() != null) {
            throw new IllegalArgumentException("API 주소에 #fragment를 포함할 수 없습니다.");
        }
        return normalizeBaseUrl(candidate, defaultValue);
    }

    static String validateUsableHttpsBaseUrl(String rawValue, String defaultValue) {
        String normalized = validateHttpsBaseUrl(rawValue, defaultValue);
        try {
            URI uri = new URI(normalized);
            if (isKnownInternalHost(uri.getHost())) {
                throw new IllegalArgumentException(
                        "기존 내부망 주소는 사용할 수 없습니다. 공개 HTTPS API 주소를 입력하세요."
                );
            }
            return normalized;
        } catch (URISyntaxException exception) {
            throw new IllegalArgumentException("API 주소 형식을 확인하세요.");
        }
    }

    static String safeUsableHttpsBaseUrl(String rawValue, String defaultValue) {
        try {
            return validateUsableHttpsBaseUrl(rawValue, defaultValue);
        } catch (IllegalArgumentException ignored) {
            return validateHttpsBaseUrl(defaultValue, defaultValue);
        }
    }

    static boolean shouldReplaceStoredEndpoint(
            int appliedVersion,
            int targetVersion,
            String storedValue,
            String defaultValue
    ) {
        if (appliedVersion >= targetVersion) {
            return false;
        }
        if (storedValue == null || storedValue.trim().isEmpty()) {
            return true;
        }
        try {
            validateUsableHttpsBaseUrl(storedValue, defaultValue);
            return false;
        } catch (IllegalArgumentException ignored) {
            return true;
        }
    }

    static boolean sameHttpsEndpoint(String first, String second) {
        try {
            URI firstUri = new URI(validateHttpsBaseUrl(first, first));
            URI secondUri = new URI(validateHttpsBaseUrl(second, second));
            return firstUri.getHost().equalsIgnoreCase(secondUri.getHost())
                    && effectivePort(firstUri) == effectivePort(secondUri)
                    && normalizedPath(firstUri).equals(normalizedPath(secondUri));
        } catch (Exception ignored) {
            return false;
        }
    }

    private static int effectivePort(URI uri) {
        return uri.getPort() < 0 ? 443 : uri.getPort();
    }

    private static String normalizedPath(URI uri) {
        String path = uri.getRawPath();
        if (path == null || path.isEmpty() || "/".equals(path)) {
            return "";
        }
        return path.replaceAll("/+$", "");
    }

    private static boolean isKnownInternalHost(String rawHost) {
        if (rawHost == null) {
            return true;
        }
        String host = rawHost.trim().toLowerCase(Locale.ROOT);
        while (host.endsWith(".")) {
            host = host.substring(0, host.length() - 1);
        }
        if (host.startsWith("[") && host.endsWith("]")) {
            host = host.substring(1, host.length() - 1);
        }

        if (host.isEmpty()
                || "bot".equals(host)
                || "localhost".equals(host)
                || "0.0.0.0".equals(host)
                || "::".equals(host)
                || "::1".equals(host)
                || host.endsWith(".local")
                || host.endsWith(".lan")
                || "ts.net".equals(host)
                || host.endsWith(".ts.net")) {
            return true;
        }

        String[] octets = host.split("\\.", -1);
        if (octets.length != 4) {
            return host.contains(":")
                    && (host.startsWith("fc")
                    || host.startsWith("fd")
                    || host.startsWith("fe8")
                    || host.startsWith("fe9")
                    || host.startsWith("fea")
                    || host.startsWith("feb"));
        }
        try {
            int first = parseIpv4Octet(octets[0]);
            int second = parseIpv4Octet(octets[1]);
            parseIpv4Octet(octets[2]);
            parseIpv4Octet(octets[3]);
            return first == 10
                    || first == 127
                    || (first == 169 && second == 254)
                    || (first == 172 && second >= 16 && second <= 31)
                    || (first == 192 && second == 168)
                    || (first == 100 && second >= 64 && second <= 127);
        } catch (NumberFormatException ignored) {
            return false;
        }
    }

    private static int parseIpv4Octet(String value) {
        int parsed = Integer.parseInt(value);
        if (parsed < 0 || parsed > 255) {
            throw new NumberFormatException("IPv4 octet out of range");
        }
        return parsed;
    }
}
