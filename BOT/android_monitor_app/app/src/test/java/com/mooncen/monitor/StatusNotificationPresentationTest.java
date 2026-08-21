package com.mooncen.monitor;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public class StatusNotificationPresentationTest {
    @Test
    public void healthyCoreSummaryIsCompactAndReadable() {
        StatusNotificationPresentation.Snapshot snapshot =
                StatusNotificationPresentation.summary(status(
                        CoreStatusSnapshot.State.HEALTHY,
                        services(CoreStatusSnapshot.State.HEALTHY)
                ));

        assertEquals("문센 핵심 서비스 · 정상", snapshot.title);
        assertEquals("DB·FRONT·BACKEND·CRAWLER 정상 · Primary cloud", snapshot.text);
        assertEquals(StatusNotificationPresentation.LEVEL_HEALTHY, snapshot.level);
        assertTrue(snapshot.bigText.contains("Primary 관측: cloud · 예상: cloud"));
        assertTrue(snapshot.bigText.contains("BACKEND: 정상 · 실행 정상 · 기능 정상 · cloud"));
    }

    @Test
    public void criticalAndUnknownServicesRemainDistinct() {
        List<CoreStatusSnapshot.Service> services = services(CoreStatusSnapshot.State.HEALTHY);
        services.set(2, service(
                CoreStatusSnapshot.BACKEND,
                CoreStatusSnapshot.State.CRITICAL,
                true,
                false
        ));
        services.set(3, service(
                CoreStatusSnapshot.CRAWLER,
                CoreStatusSnapshot.State.UNKNOWN,
                true,
                null
        ));

        StatusNotificationPresentation.Snapshot snapshot =
                StatusNotificationPresentation.summary(status(
                        CoreStatusSnapshot.State.HEALTHY,
                        services
                ));

        assertEquals("문센 핵심 서비스 · 장애", snapshot.title);
        assertTrue(snapshot.text.contains("BACKEND 장애"));
        assertTrue(snapshot.text.contains("CRAWLER 확인 불가"));
        assertEquals(StatusNotificationPresentation.LEVEL_CRITICAL, snapshot.level);
        assertTrue(snapshot.bigText.contains("BACKEND: 장애 · 실행 정상 · 기능 실패"));
        assertTrue(snapshot.bigText.contains("CRAWLER: 확인 불가 · 실행 정상 · 기능 확인 불가"));
    }

    @Test
    public void runtimeWarningDoesNotBecomeConfirmedCriticalOutage() {
        List<CoreStatusSnapshot.Service> services = services(CoreStatusSnapshot.State.HEALTHY);
        services.set(1, service(
                CoreStatusSnapshot.FRONTEND,
                CoreStatusSnapshot.State.WARNING,
                false,
                true
        ));

        StatusNotificationPresentation.Snapshot snapshot =
                StatusNotificationPresentation.summary(status(
                        CoreStatusSnapshot.State.HEALTHY,
                        services
                ));

        assertEquals("문센 핵심 서비스 · 주의", snapshot.title);
        assertEquals(StatusNotificationPresentation.LEVEL_WARNING, snapshot.level);
        assertTrue(snapshot.text.contains("FRONT 주의"));
        assertTrue(snapshot.bigText.contains("FRONT: 주의 · 실행 실패 · 기능 정상"));
    }

    @Test
    public void unavailableCoreStatusIsWarningInsteadOfConfirmedOutage() {
        StatusNotificationPresentation.Snapshot snapshot =
                StatusNotificationPresentation.unavailable("");

        assertEquals("문센 핵심 서비스 · 확인 불가", snapshot.title);
        assertEquals(StatusNotificationPresentation.LEVEL_WARNING, snapshot.level);
        assertTrue(snapshot.bigText.contains("상태 API에 연결할 수 없습니다."));
    }

    private static CoreStatusSnapshot status(
            CoreStatusSnapshot.State primaryState,
            List<CoreStatusSnapshot.Service> services
    ) {
        Map<String, String> nodes = new LinkedHashMap<>();
        for (String key : CoreStatusSnapshot.SERVICE_ORDER) {
            nodes.put(key, "cloud");
        }
        return CoreStatusSnapshot.create(
                "2026-08-07T00:00:00Z",
                new CoreStatusSnapshot.Topology("production", "cloud", nodes),
                new CoreStatusSnapshot.Primary(
                        "cloud",
                        "cloud",
                        primaryState,
                        primaryState == CoreStatusSnapshot.State.HEALTHY,
                        true,
                        true,
                        true,
                        Arrays.asList("cloud")
                ),
                services
        );
    }

    private static List<CoreStatusSnapshot.Service> services(CoreStatusSnapshot.State state) {
        List<CoreStatusSnapshot.Service> result = new ArrayList<>();
        for (String key : CoreStatusSnapshot.SERVICE_ORDER) {
            result.add(service(key, state, true, true));
        }
        return result;
    }

    private static CoreStatusSnapshot.Service service(
            String key,
            CoreStatusSnapshot.State state,
            Boolean runtimeOk,
            Boolean functionalOk
    ) {
        return new CoreStatusSnapshot.Service(
                key,
                "cloud",
                Arrays.asList("cloud"),
                runtimeOk,
                functionalOk,
                state == CoreStatusSnapshot.State.HEALTHY,
                state,
                "",
                "2026-08-07T00:00:00Z"
        );
    }
}
