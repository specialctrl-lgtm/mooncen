package com.mooncen.monitor;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public class AppConfigTest {
    @Test
    public void coreEndpointUsesNormalizedHttpsBaseUrl() {
        assertEquals(
                "https://mon.binary.kr/api/monitoring/core",
                AppConfig.coreUrl("https://mon.binary.kr/")
        );
    }

    @Test
    public void crawlerAndMooncenUseDistinctEndpoints() {
        assertEquals(
                "https://mon.binary.kr/api/monitoring/crawler",
                AppConfig.crawlerUrl("https://mon.binary.kr/")
        );
        assertEquals(
                "https://mon.binary.kr/api/monitoring/mooncen",
                AppConfig.mooncenUrl("https://mon.binary.kr/")
        );
    }
}
