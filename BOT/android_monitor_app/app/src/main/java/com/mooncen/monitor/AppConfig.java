package com.mooncen.monitor;

import android.content.SharedPreferences;

public final class AppConfig {
    public static final String PREFS_NAME = "mooncen_monitor";
    public static final String KEY_BASE_URL = "base_url";
    public static final String KEY_APP_TOKEN = "app_token";
    public static final String KEY_DISMISSED_UPDATE_CODE = "dismissed_update_code";
    public static final String KEY_LAST_UPDATE_CHECK_AT = "last_update_check_at";
    private static final String KEY_CONFIG_MIGRATION_VERSION = "config_migration_version";
    private static final int CONFIG_MIGRATION_VERSION = 2;

    public static final String DEFAULT_BASE_URL = "https://mon.binary.kr";

    private AppConfig() {
    }

    public static String getBaseUrl(SharedPreferences prefs) {
        String value = prefs.getString(KEY_BASE_URL, DEFAULT_BASE_URL);
        return EndpointPolicy.safeUsableHttpsBaseUrl(value, DEFAULT_BASE_URL);
    }

    public static String normalizeBaseUrl(String value) {
        return EndpointPolicy.normalizeBaseUrl(value, DEFAULT_BASE_URL);
    }

    public static synchronized boolean migrateLegacyBaseUrl(SharedPreferences prefs) {
        int appliedVersion = prefs.getInt(KEY_CONFIG_MIGRATION_VERSION, 0);
        String storedValue = prefs.getString(KEY_BASE_URL, null);
        boolean shouldMigrate = EndpointPolicy.shouldReplaceStoredEndpoint(
                appliedVersion,
                CONFIG_MIGRATION_VERSION,
                storedValue,
                DEFAULT_BASE_URL
        );

        if (appliedVersion == CONFIG_MIGRATION_VERSION && !shouldMigrate) {
            return false;
        }

        SharedPreferences.Editor editor = prefs.edit()
                .putInt(KEY_CONFIG_MIGRATION_VERSION, CONFIG_MIGRATION_VERSION);
        if (shouldMigrate) {
            editor.putString(KEY_BASE_URL, DEFAULT_BASE_URL);
        }
        editor.apply();
        return shouldMigrate;
    }

    public static boolean isPublicStatusEndpoint(SharedPreferences prefs) {
        return EndpointPolicy.sameHttpsEndpoint(getBaseUrl(prefs), DEFAULT_BASE_URL);
    }

    public static String coreUrl(SharedPreferences prefs) {
        return coreUrl(getBaseUrl(prefs));
    }

    public static String coreUrl(String baseUrl) {
        return normalizeBaseUrl(baseUrl) + "/api/monitoring/core";
    }

    public static String crawlerUrl(SharedPreferences prefs) {
        return crawlerUrl(getBaseUrl(prefs));
    }

    public static String crawlerUrl(String baseUrl) {
        return normalizeBaseUrl(baseUrl) + "/api/monitoring/crawler";
    }

    public static String mooncenUrl(SharedPreferences prefs) {
        return mooncenUrl(getBaseUrl(prefs));
    }

    public static String mooncenUrl(String baseUrl) {
        return normalizeBaseUrl(baseUrl) + "/api/monitoring/mooncen";
    }

    public static String serversUrl(SharedPreferences prefs) {
        return getBaseUrl(prefs) + "/api/monitoring/servers";
    }

    public static String tailscaleUrl(SharedPreferences prefs) {
        return getBaseUrl(prefs) + "/api/monitoring/tailscale";
    }

    public static String operationsUrl(SharedPreferences prefs) {
        return getBaseUrl(prefs) + "/api/operation/actions";
    }

    public static String runOperationUrl(SharedPreferences prefs) {
        return getBaseUrl(prefs) + "/api/operation/run";
    }

    public static String getToken(SharedPreferences prefs) {
        String value = prefs.getString(KEY_APP_TOKEN, "");
        return value == null ? "" : value.trim();
    }
}
