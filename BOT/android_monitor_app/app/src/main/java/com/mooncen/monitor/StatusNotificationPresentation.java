package com.mooncen.monitor;

final class StatusNotificationPresentation {
    static final int LEVEL_HEALTHY = 0;
    static final int LEVEL_WARNING = 1;
    static final int LEVEL_CRITICAL = 2;

    private StatusNotificationPresentation() {
    }

    static Snapshot summary(CoreStatusSnapshot status) {
        CoreStatusSnapshot.State overall = status.overallState();
        String overallLabel = stateLabel(overall);
        String primaryNode = safeNode(status.observedPrimaryNode());
        String expectedNode = safeNode(status.expectedPrimaryNode());

        StringBuilder issues = new StringBuilder();
        for (CoreStatusSnapshot.Service service : status.services) {
            if (service.state == CoreStatusSnapshot.State.HEALTHY) {
                continue;
            }
            appendPart(
                    issues,
                    CoreStatusSnapshot.serviceLabel(service.key) + " " + stateLabel(service.state)
            );
        }
        if (status.primary.state != CoreStatusSnapshot.State.HEALTHY) {
            appendPart(issues, "Primary " + stateLabel(status.primary.state));
        }

        String text;
        if (issues.length() == 0) {
            text = "DB·FRONT·BACKEND·CRAWLER 정상 · Primary " + primaryNode;
        } else {
            text = issues + " · Primary " + primaryNode;
        }

        StringBuilder bigText = new StringBuilder()
                .append("Primary 관측: ").append(primaryNode)
                .append(" · 예상: ").append(expectedNode)
                .append(" · DB 쓰기: ").append(booleanLabel(status.primary.databaseWritable));
        for (CoreStatusSnapshot.Service service : status.services) {
            bigText.append('\n')
                    .append(CoreStatusSnapshot.serviceLabel(service.key))
                    .append(": ").append(stateLabel(service.state))
                    .append(" · 실행 ").append(booleanLabel(service.runtimeOk))
                    .append(" · 기능 ").append(booleanLabel(service.functionalOk))
                    .append(" · ").append(safeNode(service.primaryNode));
        }
        return new Snapshot(
                "문센 핵심 서비스 · " + overallLabel,
                text,
                bigText.toString(),
                level(overall)
        );
    }

    static Snapshot unavailable(String detail) {
        String safeDetail = detail == null ? "" : detail.trim();
        if (safeDetail.isEmpty()) {
            safeDetail = "상태 API에 연결할 수 없습니다.";
        }
        if (safeDetail.length() > 240) {
            safeDetail = safeDetail.substring(0, 240) + "…";
        }
        return new Snapshot(
                "문센 핵심 서비스 · 확인 불가",
                "핵심 서비스 상태를 확인할 수 없습니다.",
                "마지막 핵심 상태 확인에 실패했습니다.\n" + safeDetail,
                LEVEL_WARNING
        );
    }

    static Snapshot checking() {
        return new Snapshot(
                "문센 핵심 서비스 · 확인 중",
                "Primary와 핵심 서비스 상태를 불러오는 중입니다.",
                "앱을 열거나 다음 백그라운드 확인이 실행되면 상태가 갱신됩니다.",
                LEVEL_WARNING
        );
    }

    static String stateLabel(CoreStatusSnapshot.State state) {
        if (state == CoreStatusSnapshot.State.HEALTHY) {
            return "정상";
        }
        if (state == CoreStatusSnapshot.State.CRITICAL) {
            return "장애";
        }
        if (state == CoreStatusSnapshot.State.WARNING) {
            return "주의";
        }
        return "확인 불가";
    }

    static String booleanLabel(Boolean value) {
        if (value == null) {
            return "확인 불가";
        }
        return value ? "정상" : "실패";
    }

    private static int level(CoreStatusSnapshot.State state) {
        if (state == CoreStatusSnapshot.State.HEALTHY) {
            return LEVEL_HEALTHY;
        }
        if (state == CoreStatusSnapshot.State.CRITICAL) {
            return LEVEL_CRITICAL;
        }
        return LEVEL_WARNING;
    }

    private static String safeNode(String value) {
        return value == null || value.trim().isEmpty() ? "확인 불가" : value.trim();
    }

    private static void appendPart(StringBuilder body, String value) {
        if (body.length() > 0) {
            body.append(" · ");
        }
        body.append(value);
    }

    static final class Snapshot {
        final String title;
        final String text;
        final String bigText;
        final int level;

        Snapshot(String title, String text, String bigText, int level) {
            this.title = title;
            this.text = text;
            this.bigText = bigText;
            this.level = level;
        }
    }
}
