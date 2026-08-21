package com.mooncen.monitor;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public class CrawlerMonitoringPresentationTest {
    @Test
    public void unavailableAndRealZeroRemainDistinct() {
        assertEquals("확인 불가", CrawlerMonitoringPresentation.count(null, "건"));
        assertEquals("0건", CrawlerMonitoringPresentation.count(0L, "건"));
        assertEquals("확인 불가", CrawlerMonitoringPresentation.duration(null));
        assertEquals("0.0초", CrawlerMonitoringPresentation.duration(0.0));
        assertEquals("확인 불가", CrawlerMonitoringPresentation.temperature(null));
        assertEquals("0.0°C", CrawlerMonitoringPresentation.temperature(0.0));
    }

    @Test
    public void statusAndRoleLabelsUseCrawlerTerms() {
        assertEquals("부분 성공", CrawlerMonitoringPresentation.latestStatusLabel("partial_success"));
        assertEquals("Provider 없음", CrawlerMonitoringPresentation.latestStatusLabel("zero_provider"));
        assertEquals("확인 불가", CrawlerMonitoringPresentation.latestStatusLabel("invalid"));
        assertEquals("현재 실행", CrawlerMonitoringPresentation.nodeRoleLabel("runtime"));
        assertEquals("목표 워커", CrawlerMonitoringPresentation.nodeRoleLabel("target"));
        assertEquals("중앙 제어", CrawlerMonitoringPresentation.nodeRoleLabel("control"));
    }

    @Test
    public void performanceUnitsAreReadable() {
        assertEquals("59.5초", CrawlerMonitoringPresentation.duration(59.5));
        assertEquals("2.0분", CrawlerMonitoringPresentation.duration(120.0));
        assertEquals("2.0시간", CrawlerMonitoringPresentation.duration(7200.0));
        assertEquals("87.5%", CrawlerMonitoringPresentation.percentage(87.5));
        assertEquals("41.2°C", CrawlerMonitoringPresentation.temperature(41.2));
        assertEquals("58분", CrawlerMonitoringPresentation.age(3480.0));
        assertEquals("0.42", CrawlerMonitoringPresentation.load(0.42));
    }

    @Test
    public void temperatureRequiresOnlineObservedSensorEvidence() {
        CrawlerMonitoringSnapshot.Node online = node(true, "up", 41.2, true);
        CrawlerMonitoringSnapshot.Node unknown = node(true, "unknown", 41.2, true);
        CrawlerMonitoringSnapshot.Node noSensorEvidence = node(true, "up", null, false);

        assertEquals("41.2°C", CrawlerMonitoringPresentation.nodeTemperatureLabel(online));
        assertEquals("확인 불가", CrawlerMonitoringPresentation.nodeTemperatureLabel(unknown));
        assertEquals(
                "확인 불가",
                CrawlerMonitoringPresentation.nodeTemperatureLabel(noSensorEvidence)
        );
    }

    private static CrawlerMonitoringSnapshot.Node node(
            boolean available,
            String status,
            Double temperature,
            boolean temperatureAvailable
    ) {
        return new CrawlerMonitoringSnapshot.Node(
                true,
                "gen1crawler",
                "target",
                available,
                status,
                temperature,
                temperatureAvailable,
                10.0,
                20.0,
                0.5,
                30.0,
                8L,
                ""
        );
    }
}
