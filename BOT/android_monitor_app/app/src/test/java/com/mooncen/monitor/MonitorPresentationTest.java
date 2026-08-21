package com.mooncen.monitor;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;

import org.junit.Test;

public class MonitorPresentationTest {
    @Test
    public void inactiveOneShotWithFreshSuccessIsNotStopped() {
        String badge = MonitorPresentation.backupBadge(
                true,
                false,
                true,
                true,
                true,
                "healthy"
        );

        assertEquals("최신", badge);
        assertEquals(
                MonitorPresentation.LEVEL_HEALTHY,
                MonitorPresentation.backupLevel(true, false, true, true, true, "healthy")
        );
        String historicalBadge = MonitorPresentation.backupBadge(
                true,
                false,
                false,
                false,
                true,
                ""
        );
        assertEquals("성공 기록", historicalBadge);
        assertFalse(historicalBadge.contains("중지"));
    }

    @Test
    public void staleOrFailedBackupGetsVisibleSeverity() {
        assertEquals(
                "갱신 필요",
                MonitorPresentation.backupBadge(true, false, true, false, true, "healthy")
        );
        assertEquals(
                MonitorPresentation.LEVEL_WARNING,
                MonitorPresentation.backupLevel(true, false, true, false, true, "healthy")
        );
        assertEquals(
                MonitorPresentation.LEVEL_CRITICAL,
                MonitorPresentation.backupLevel(true, false, false, false, true, "failed")
        );
        assertEquals(
                MonitorPresentation.LEVEL_WARNING,
                MonitorPresentation.backupLevel(true, false, true, false, true, "stale")
        );
    }

    @Test
    public void peerBadgeAndConnectionAreHumanReadable() {
        assertEquals("온라인", MonitorPresentation.peerBadge(true, true));
        assertEquals("온라인", MonitorPresentation.peerBadge(true, false));
        assertEquals(
                MonitorPresentation.LEVEL_HEALTHY,
                MonitorPresentation.peerLevel(true, false)
        );
        assertEquals("오프라인", MonitorPresentation.peerBadge(false, false));
        assertEquals("오프라인", MonitorPresentation.peerBadge(false, true));
        assertEquals("직접 연결", MonitorPresentation.connectionLabel("direct"));
        assertEquals("릴레이", MonitorPresentation.connectionLabel("DERP"));
        assertEquals("릴레이", MonitorPresentation.connectionLabel("derp-seoul"));
    }

    @Test
    public void dnsShortAliasWinsOverRawTailscaleName() {
        assertEquals(
                "cloud",
                MonitorPresentation.tailscaleDisplayName(
                        "cloud.example.ts.net.",
                        "raw-cloud-node",
                        "fallback"
                )
        );
        assertEquals(
                "wtr-linux",
                MonitorPresentation.tailscaleDisplayName("", "wtr-linux", "fallback")
        );
    }

    @Test
    public void tailscaleSnapshotErrorsAreLocalized() {
        assertEquals(
                "Tailscale 상태 스냅샷이 아직 생성되지 않았습니다.",
                MonitorPresentation.tailscaleErrorMessage("snapshot_missing")
        );
        assertEquals(
                "Tailscale 상태 스냅샷을 읽을 수 없습니다.",
                MonitorPresentation.tailscaleErrorMessage("snapshot_unreadable")
        );
        assertEquals(
                "Tailscale 상태 스냅샷 형식이 올바르지 않습니다.",
                MonitorPresentation.tailscaleErrorMessage("snapshot_invalid")
        );
        assertEquals(
                "Tailscale 상태 정보가 오래되었습니다. 수집 상태를 확인하세요.",
                MonitorPresentation.tailscaleErrorMessage("snapshot_stale")
        );
    }

    @Test
    public void aiServerRoleIsClearlyLabeled() {
        assertEquals("AI", MonitorPresentation.roleLabel("ai"));
        assertEquals("AI", MonitorPresentation.roleLabel("AI-SERVER"));
        assertEquals("Linux", MonitorPresentation.roleLabel("linux"));
        assertEquals("DB", MonitorPresentation.roleLabel("db"));
        assertEquals("WEB", MonitorPresentation.roleLabel("web"));
        assertEquals("크롤러", MonitorPresentation.roleLabel("crawler"));
        assertEquals("이전 서버", MonitorPresentation.roleLabel("legacy"));
        assertEquals("-", MonitorPresentation.roleLabel(""));
    }
}
