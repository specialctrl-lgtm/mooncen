package com.mooncen.monitor;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import org.junit.Test;

public class EndpointPolicyTest {
    private static final String DEFAULT_URL = "https://mon.binary.kr";
    private static final String LEGACY_URL = "http://bot:8088";

    @Test
    public void validatesAndNormalizesHttpsUrl() {
        assertEquals(
                DEFAULT_URL,
                EndpointPolicy.validateHttpsBaseUrl("  https://mon.binary.kr///  ", DEFAULT_URL)
        );
        assertEquals(
                DEFAULT_URL,
                EndpointPolicy.validateHttpsBaseUrl("   ", DEFAULT_URL)
        );
    }

    @Test
    public void rejectsHttpAndUnsafeUrlComponents() {
        assertRejected("http://mon.binary.kr");
        assertRejected("https://user@mon.binary.kr");
        assertRejected("https://mon.binary.kr?token=secret");
        assertRejected("https://mon.binary.kr#status");
    }

    @Test
    public void migrationReplacesInvalidAndKnownInternalEndpoints() {
        assertTrue(shouldReplace(0, LEGACY_URL));
        assertTrue(shouldReplace(1, LEGACY_URL + "/"));
        assertTrue(shouldReplace(1, "http://mooncen.example"));
        assertTrue(shouldReplace(1, "https://device.tailnet.ts.net"));
        assertTrue(shouldReplace(1, "https://100.90.1.2:8088"));
        assertTrue(shouldReplace(1, "not a url"));
        assertTrue(shouldReplace(1, null));
    }

    @Test
    public void migrationPreservesValidCustomHttpsAndRunsOnlyOnce() {
        assertFalse(shouldReplace(1, "https://private.example"));
        assertFalse(EndpointPolicy.shouldReplaceStoredEndpoint(
                2,
                2,
                LEGACY_URL,
                DEFAULT_URL
        ));
    }

    @Test
    public void safeStoredEndpointFallsBackToPublicHttps() {
        assertEquals(
                "https://private.example",
                EndpointPolicy.safeUsableHttpsBaseUrl(
                        "https://private.example/",
                        DEFAULT_URL
                )
        );
        assertEquals(
                DEFAULT_URL,
                EndpointPolicy.safeUsableHttpsBaseUrl(LEGACY_URL, DEFAULT_URL)
        );
        assertEquals(
                DEFAULT_URL,
                EndpointPolicy.safeUsableHttpsBaseUrl(
                        "https://host.ts.net",
                        DEFAULT_URL
                )
        );
    }

    @Test
    public void comparesEquivalentPublicEndpoints() {
        assertTrue(EndpointPolicy.sameHttpsEndpoint(
                "https://MON.binary.kr:443/",
                DEFAULT_URL
        ));
        assertFalse(EndpointPolicy.sameHttpsEndpoint(
                "https://private.example",
                DEFAULT_URL
        ));
        assertFalse(EndpointPolicy.sameHttpsEndpoint(
                "http://mon.binary.kr",
                DEFAULT_URL
        ));
    }

    private static void assertRejected(String value) {
        try {
            EndpointPolicy.validateHttpsBaseUrl(value, DEFAULT_URL);
            fail("Expected URL to be rejected: " + value);
        } catch (IllegalArgumentException expected) {
            // Expected.
        }
    }

    private static boolean shouldReplace(int appliedVersion, String storedValue) {
        return EndpointPolicy.shouldReplaceStoredEndpoint(
                appliedVersion,
                2,
                storedValue,
                DEFAULT_URL
        );
    }
}
