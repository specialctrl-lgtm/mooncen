package com.mooncen.monitor;

import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.net.SocketTimeoutException;
import java.net.UnknownHostException;

import javax.net.ssl.SSLException;

public class ApiErrorMessageTest {
    @Test
    public void mapsImportantHttpStatusesToKoreanGuidance() {
        assertContains(
                ApiErrorMessage.from(new ApiClient.ApiException(401, "HTTP 401")),
                "토큰"
        );
        assertContains(
                ApiErrorMessage.from(new ApiClient.ApiException(403, "HTTP 403")),
                "접근을 거부"
        );
        assertContains(
                ApiErrorMessage.from(new ApiClient.ApiException(404, "HTTP 404")),
                "경로"
        );
        assertContains(
                ApiErrorMessage.from(new ApiClient.ApiException(302, "HTTP 302")),
                "리디렉션"
        );
    }

    @Test
    public void mapsDnsTimeoutAndTlsFailuresToKoreanGuidance() {
        assertContains(ApiErrorMessage.from(new UnknownHostException()), "서버 주소");
        assertContains(ApiErrorMessage.from(new SocketTimeoutException()), "시간이 초과");
        assertContains(ApiErrorMessage.from(new SSLException("certificate")), "인증서");
    }

    private static void assertContains(String actual, String expected) {
        assertTrue(
                "Expected <" + actual + "> to contain <" + expected + ">",
                actual.contains(expected)
        );
    }
}
