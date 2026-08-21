package com.mooncen.monitor;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class AppUpdatePolicyTest {
    @Test
    public void onlyNewerVersionIsAnUpdate() {
        assertTrue(AppUpdatePolicy.isUpdateAvailable(4, 5));
        assertFalse(AppUpdatePolicy.isUpdateAvailable(5, 5));
        assertFalse(AppUpdatePolicy.isUpdateAvailable(6, 5));
    }

    @Test
    public void onlyPinnedHttpsApkUrlIsAllowed() {
        assertTrue(AppUpdatePolicy.isAllowedApkUrl(AppUpdatePolicy.APK_URL));
        assertFalse(AppUpdatePolicy.isAllowedApkUrl(
                "http://mon.binary.kr/android/mooncen-monitor.apk"
        ));
        assertFalse(AppUpdatePolicy.isAllowedApkUrl(
                "https://evil.example/android/mooncen-monitor.apk"
        ));
        assertFalse(AppUpdatePolicy.isAllowedApkUrl(
                "https://mon.binary.kr/android/mooncen-monitor.apk?next=evil"
        ));
        assertFalse(AppUpdatePolicy.isAllowedApkUrl(
                "https://mon.binary.kr/android/../installers/install.sh"
        ));
    }

    @Test
    public void sha256MustBeCompleteHexDigest() {
        assertTrue(AppUpdatePolicy.isValidSha256(
                "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        ));
        assertFalse(AppUpdatePolicy.isValidSha256("012345"));
        assertFalse(AppUpdatePolicy.isValidSha256(
                "z123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        ));
    }
}
