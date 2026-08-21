package com.mooncen.monitor;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.text.InputType;
import android.text.TextUtils;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.Locale;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;

public class MainActivity extends Activity {
    private static final int TAB_CRAWLER = 0;
    private static final int TAB_MOONCEN = 1;
    private static final int TAB_SERVER = 2;
    private static final int TAB_OPERATION = 3;
    private static final long AUTOMATIC_UPDATE_CHECK_DELAY_MS = 2_500L;

    private static final int COLOR_BACKGROUND = 0xff0b1014;
    private static final int COLOR_SURFACE = 0xff131a20;
    private static final int COLOR_SURFACE_ALT = 0xff1a232b;
    private static final int COLOR_BORDER = 0xff2a3943;
    private static final int COLOR_TEXT = 0xfff2f7f4;
    private static final int COLOR_MUTED = 0xff9caca7;
    private static final int COLOR_ACCENT = 0xff63d6c2;
    private static final int COLOR_HEALTHY = 0xff55c995;
    private static final int COLOR_WARNING = 0xffffc857;
    private static final int COLOR_CRITICAL = 0xffff737d;
    private static final int COLOR_INFO = 0xff82b1ff;

    private final ExecutorService executor = Executors.newFixedThreadPool(3);
    private final ExecutorService refreshExecutor = Executors.newSingleThreadExecutor();
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final Set<ApiClient.RequestCancellation> pauseCancellations =
            ConcurrentHashMap.newKeySet();
    private final Set<ApiClient.RequestCancellation> destroyCancellations =
            ConcurrentHashMap.newKeySet();

    private SharedPreferences prefs;
    private ProgressBar progress;
    private LinearLayout tabBar;
    private LinearLayout content;
    private ScrollView scrollView;
    private TextView updatedView;
    private volatile int currentTab = TAB_CRAWLER;
    private int renderedTab = -1;
    private volatile long latestRequestId;
    private volatile boolean screenActive;
    private boolean operationRunning;
    private boolean updateCheckRunning;
    private boolean operationResultPending;
    private JSONObject pendingOperationResult;
    private Exception pendingOperationException;
    private String pendingOperationFingerprint;
    private Future<?> refreshTask;
    private ApiClient.RequestCancellation refreshCancellation;
    private long lastCoreRefreshCompletedAt;
    private long lastHandledCoreObservationSequence;
    private boolean lastCoreUnavailable;

    private final Runnable autoRefreshRunnable = new Runnable() {
        @Override
        public void run() {
            if (!canAutoRefreshCurrentTab()) {
                return;
            }
            refresh(true);
            restartAutoRefreshTimer();
        }
    };

    private final Runnable automaticUpdateCheckRunnable = () -> {
        if (!screenActive
                || prefs == null
                || AppConfig.getToken(prefs).isEmpty()) {
            return;
        }
        checkForAppUpdate(false);
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = getSharedPreferences(AppConfig.PREFS_NAME, Context.MODE_PRIVATE);
        boolean connectionMigrated = AppConfig.migrateLegacyBaseUrl(prefs);
        if (connectionMigrated) {
            RecentCoreSnapshotCache.clear();
            CoreRequestCoordinator.clear();
            MonitorJobService.resetProblemState(this);
            MonitorStatusNotification.reset(this);
        }
        requestNotificationPermission();
        buildUi();
        if (connectionMigrated || AppConfig.getToken(prefs).isEmpty()) {
            mainHandler.post(this::showSettingsDialog);
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        screenActive = true;
        if (MonitorJobService.schedule(this)) {
            MonitorStatusNotification.ensureVisible(this);
        }
        boolean deliveredOperationResult = deliverPendingOperationResult();
        long latestCoreObservationSequence =
                RecentCoreSnapshotCache.latestAcceptedObservationSequence();
        boolean coreObservationChanged =
                latestCoreObservationSequence > lastHandledCoreObservationSequence;
        if (coreObservationChanged
                && lastCoreUnavailable
                && RecentCoreSnapshotCache.latestAcceptedObservationUnavailable()) {
            // Foreground and Job callers can finish the same shared failed GET with different
            // observation numbers. The screen already represents this unavailable state.
            lastHandledCoreObservationSequence = latestCoreObservationSequence;
            coreObservationChanged = false;
        }
        if (!deliveredOperationResult
                && !AppConfig.getToken(prefs).isEmpty()
                && isAutoRefreshTab(currentTab)
                && (!isCoreTab(currentTab)
                || coreObservationChanged
                || PowerPolicy.isForegroundRefreshDue(
                        lastCoreRefreshCompletedAt,
                        System.currentTimeMillis()
                ))) {
            refresh(true);
        }
        restartAutoRefreshTimer();
        scheduleAutomaticUpdateCheck();
    }

    @Override
    protected void onPause() {
        screenActive = false;
        mainHandler.removeCallbacks(autoRefreshRunnable);
        mainHandler.removeCallbacks(automaticUpdateCheckRunnable);
        cancelRefreshTask();
        cancelRequests(pauseCancellations);
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        mainHandler.removeCallbacksAndMessages(null);
        cancelRefreshTask();
        cancelRequests(pauseCancellations);
        cancelRequests(destroyCancellations);
        refreshExecutor.shutdownNow();
        executor.shutdownNow();
        super.onDestroy();
    }

    private void buildUi() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(COLOR_BACKGROUND);
        applySystemBarInsets(root);

        LinearLayout bar = new LinearLayout(this);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.setPadding(dp(16), dp(8), dp(10), dp(8));
        bar.setBackgroundColor(COLOR_SURFACE);
        bar.setMinimumHeight(dp(64));

        LinearLayout brand = new LinearLayout(this);
        brand.setOrientation(LinearLayout.VERTICAL);
        brand.setGravity(Gravity.CENTER_VERTICAL);
        brand.setMinimumHeight(dp(48));

        TextView title = new TextView(this);
        title.setText("문센 모니터");
        title.setTextColor(COLOR_TEXT);
        title.setTextSize(19);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        title.setSingleLine(true);
        title.setEllipsize(TextUtils.TruncateAt.END);
        TextView subtitle = smallText("핵심 운영 상태");
        subtitle.setTextSize(11);
        subtitle.setSingleLine(true);
        subtitle.setEllipsize(TextUtils.TruncateAt.END);
        subtitle.setPadding(0, dp(1), 0, 0);
        brand.addView(title);
        brand.addView(subtitle);
        bar.addView(brand, new LinearLayout.LayoutParams(
                0,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                1
        ));

        TextView refresh = toolbarButton("새로고침", "↻");
        refresh.setOnClickListener(v -> {
            refresh();
            restartAutoRefreshTimer();
        });
        bar.addView(refresh);

        TextView update = toolbarButton("업데이트 확인", "⇧");
        update.setOnClickListener(v -> checkForAppUpdate(true));
        bar.addView(update);

        TextView settings = toolbarButton("설정", "설정");
        settings.setTextSize(12);
        settings.setOnClickListener(v -> showSettingsDialog());
        bar.addView(settings);

        tabBar = new LinearLayout(this);
        tabBar.setPadding(dp(10), dp(7), dp(10), dp(7));
        tabBar.setBackgroundColor(COLOR_BACKGROUND);
        tabBar.setMinimumHeight(dp(62));
        tabBar.addView(tabButton(getString(R.string.tab_crawler), TAB_CRAWLER));
        tabBar.addView(tabButton(
                getString(R.string.tab_mooncen),
                "문센 서비스",
                TAB_MOONCEN
        ));
        tabBar.addView(tabButton("서버", TAB_SERVER));
        tabBar.addView(tabButton("작업", TAB_OPERATION));

        progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progress.setIndeterminate(true);
        progress.setVisibility(View.GONE);

        scrollView = new ScrollView(this);
        scrollView.setFillViewport(true);
        content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(dp(16), dp(10), dp(16), dp(32));
        scrollView.addView(content);

        updatedView = smallText("-");
        updatedView.setGravity(Gravity.CENTER_HORIZONTAL);
        updatedView.setPadding(dp(12), dp(8), dp(12), dp(8));
        updatedView.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
        updatedView.setVisibility(View.GONE);
        root.addView(bar, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        ));
        root.addView(tabBar, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        ));
        root.addView(progress, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(3)));
        root.addView(updatedView, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        ));
        root.addView(scrollView, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1));
        setContentView(root);
        updateTabs();
    }

    @SuppressWarnings("deprecation")
    private void applySystemBarInsets(View root) {
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            int left;
            int top;
            int right;
            int bottom;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                android.graphics.Insets bars =
                        insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
                left = bars.left;
                top = bars.top;
                right = bars.right;
                bottom = bars.bottom;
            } else {
                left = insets.getSystemWindowInsetLeft();
                top = insets.getSystemWindowInsetTop();
                right = insets.getSystemWindowInsetRight();
                bottom = insets.getSystemWindowInsetBottom();
            }
            view.setPadding(left, top, right, bottom);
            return insets;
        });
        root.requestApplyInsets();
    }

    private TextView tabButton(String text, int tab) {
        return tabButton(text, text, tab);
    }

    private TextView tabButton(String text, String accessibleName, int tab) {
        TextView view = new TextView(this);
        view.setText(text);
        view.setGravity(Gravity.CENTER);
        view.setTextSize(13);
        view.setTypeface(Typeface.DEFAULT_BOLD);
        view.setSingleLine(true);
        view.setEllipsize(TextUtils.TruncateAt.END);
        view.setMinHeight(dp(48));
        view.setContentDescription(accessibleName + " 탭");
        view.setTag(tab);
        view.setFocusable(true);
        view.setOnClickListener(v -> selectTab(tab));
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                0,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                1
        );
        params.setMargins(dp(2), 0, dp(2), 0);
        view.setLayoutParams(params);
        return view;
    }

    private void selectTab(int tab) {
        if (currentTab == tab) {
            refresh();
            restartAutoRefreshTimer();
            return;
        }
        int previousTab = currentTab;
        currentTab = tab;
        cancelRefreshTask();
        updateTabs();
        scrollView.scrollTo(0, 0);
        refresh(isCoreTab(previousTab) && isCoreTab(tab));
        restartAutoRefreshTimer();
    }

    private void updateTabs() {
        for (int i = 0; i < tabBar.getChildCount(); i++) {
            TextView tab = (TextView) tabBar.getChildAt(i);
            boolean selected = tab.getTag() instanceof Integer
                    && (Integer) tab.getTag() == currentTab;
            tab.setTextColor(selected ? COLOR_BACKGROUND : COLOR_TEXT);
            tab.setBackground(roundedBackground(
                    selected ? COLOR_ACCENT : COLOR_SURFACE_ALT,
                    selected ? COLOR_ACCENT : COLOR_BORDER,
                    12
            ));
            tab.setSelected(selected);
        }
    }

    private TextView toolbarButton(String description, String text) {
        TextView view = new TextView(this);
        view.setText(text);
        view.setTextColor(COLOR_TEXT);
        view.setTextSize(18);
        view.setTypeface(Typeface.DEFAULT_BOLD);
        view.setGravity(Gravity.CENTER);
        view.setContentDescription(description);
        view.setFocusable(true);
        view.setBackground(roundedBackground(COLOR_SURFACE_ALT, COLOR_BORDER, 10));
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(dp(48), dp(48));
        params.setMargins(dp(8), 0, 0, 0);
        view.setLayoutParams(params);
        return view;
    }

    private View settingsActionButton() {
        TextView view = new TextView(this);
        view.setText("연결 설정 열기");
        view.setTextSize(15);
        view.setTypeface(Typeface.DEFAULT_BOLD);
        view.setTextColor(COLOR_BACKGROUND);
        view.setGravity(Gravity.CENTER);
        view.setBackground(roundedBackground(COLOR_HEALTHY, COLOR_HEALTHY, 12));
        view.setContentDescription("연결 설정 열기");
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                dp(48)
        );
        params.setMargins(0, 0, 0, dp(10));
        view.setLayoutParams(params);
        view.setOnClickListener(v -> showSettingsDialog());
        return view;
    }

    private void restartAutoRefreshTimer() {
        mainHandler.removeCallbacks(autoRefreshRunnable);
        if (canAutoRefreshCurrentTab()) {
            mainHandler.postDelayed(
                    autoRefreshRunnable,
                    PowerPolicy.FOREGROUND_REFRESH_INTERVAL_MS
            );
        }
    }

    private boolean canAutoRefreshCurrentTab() {
        return screenActive
                && prefs != null
                && !AppConfig.getToken(prefs).isEmpty()
                && isAutoRefreshTab(currentTab);
    }

    private void scheduleAutomaticUpdateCheck() {
        mainHandler.removeCallbacks(automaticUpdateCheckRunnable);
        if (!screenActive || AppConfig.getToken(prefs).isEmpty()) {
            return;
        }
        long now = System.currentTimeMillis();
        long lastAttemptAt = prefs.getLong(AppConfig.KEY_LAST_UPDATE_CHECK_AT, 0L);
        if (PowerPolicy.isAutomaticUpdateCheckDue(lastAttemptAt, now)) {
            mainHandler.postDelayed(
                    automaticUpdateCheckRunnable,
                    AUTOMATIC_UPDATE_CHECK_DELAY_MS
            );
        }
    }

    private void checkForAppUpdate(boolean userInitiated) {
        long attemptedAt = System.currentTimeMillis();
        long previousAttemptAt = prefs.getLong(AppConfig.KEY_LAST_UPDATE_CHECK_AT, 0L);
        if (!userInitiated) {
            if (!screenActive || AppConfig.getToken(prefs).isEmpty()) {
                return;
            }
            if (!PowerPolicy.isAutomaticUpdateCheckDue(previousAttemptAt, attemptedAt)) {
                return;
            }
        }
        if (updateCheckRunning) {
            if (userInitiated) {
                showMessage("업데이트", "이미 새 버전을 확인하고 있습니다.");
            }
            return;
        }
        updateCheckRunning = true;
        prefs.edit()
                .putLong(AppConfig.KEY_LAST_UPDATE_CHECK_AT, attemptedAt)
                .apply();
        ApiClient.RequestCancellation cancellation = trackPauseRequest();
        executor.execute(() -> {
            try {
                JSONObject data = ApiClient.getJson(
                        AppUpdatePolicy.MANIFEST_URL,
                        "",
                        cancellation
                );
                long latestVersionCode = data.optLong("version_code", 0);
                String latestVersionName = data.optString("version_name", "").trim();
                String apkUrl = data.optString("apk_url", "").trim();
                String sha256 = data.optString("sha256", "").trim();
                String notes = data.optString("notes", "").trim();
                if (latestVersionCode <= 0
                        || latestVersionName.isEmpty()
                        || !AppUpdatePolicy.isAllowedApkUrl(apkUrl)
                        || !AppUpdatePolicy.isValidSha256(sha256)) {
                    throw new IllegalStateException("업데이트 정보 형식이 올바르지 않습니다.");
                }

                long currentVersionCode = currentVersionCode();
                boolean updateAvailable = AppUpdatePolicy.isUpdateAvailable(
                        currentVersionCode,
                        latestVersionCode
                );
                long dismissedVersion = prefs.getLong(
                        AppConfig.KEY_DISMISSED_UPDATE_CODE,
                        0
                );
                mainHandler.post(() -> {
                    updateCheckRunning = false;
                    if (!screenActive || isDestroyed() || isFinishing()) {
                        return;
                    }
                    if (!updateAvailable) {
                        if (userInitiated) {
                            showMessage(
                                    "업데이트",
                                    "현재 최신 버전입니다.\n버전 " + currentVersionName()
                            );
                        }
                        return;
                    }
                    if (!userInitiated && dismissedVersion == latestVersionCode) {
                        return;
                    }
                    showUpdateDialog(
                            latestVersionCode,
                            latestVersionName,
                            apkUrl,
                            sha256,
                            notes
                    );
                });
            } catch (Exception exception) {
                mainHandler.post(() -> {
                    updateCheckRunning = false;
                    if (userInitiated
                            && screenActive
                            && !isDestroyed()
                            && !isFinishing()) {
                        showMessage(
                                "업데이트 확인 실패",
                                ApiErrorMessage.from(exception)
                        );
                    }
                });
            } finally {
                if (cancellation.isCancelled()
                        && prefs.getLong(AppConfig.KEY_LAST_UPDATE_CHECK_AT, 0L) == attemptedAt) {
                    prefs.edit()
                            .putLong(AppConfig.KEY_LAST_UPDATE_CHECK_AT, previousAttemptAt)
                            .apply();
                    mainHandler.post(() -> {
                        if (screenActive && !isDestroyed() && !isFinishing()) {
                            scheduleAutomaticUpdateCheck();
                        }
                    });
                }
                releaseRequest(cancellation);
            }
        });
    }

    private void showUpdateDialog(
            long latestVersionCode,
            String latestVersionName,
            String apkUrl,
            String sha256,
            String notes
    ) {
        StringBuilder message = new StringBuilder();
        message.append("현재 버전: ").append(currentVersionName())
                .append("\n새 버전: ").append(latestVersionName);
        if (!notes.isEmpty()) {
            message.append("\n\n").append(notes);
        }
        message.append("\n\nSHA-256: ").append(sha256, 0, 12).append("…")
                .append("\n다운로드 후 Android 설치 확인이 필요합니다.");

        new AlertDialog.Builder(this)
                .setTitle("새 버전이 있습니다")
                .setMessage(message.toString())
                .setPositiveButton("다운로드", (dialog, which) -> openApkDownload(apkUrl))
                .setNegativeButton("나중에", (dialog, which) ->
                        prefs.edit()
                                .putLong(
                                        AppConfig.KEY_DISMISSED_UPDATE_CODE,
                                        latestVersionCode
                                )
                                .apply()
                )
                .show();
    }

    private void openApkDownload(String apkUrl) {
        if (!AppUpdatePolicy.isAllowedApkUrl(apkUrl)) {
            showMessage("다운로드 실패", "허용되지 않은 APK 주소입니다.");
            return;
        }
        Intent intent = new Intent(Intent.ACTION_VIEW, Uri.parse(apkUrl));
        intent.addCategory(Intent.CATEGORY_BROWSABLE);
        try {
            startActivity(intent);
        } catch (Exception exception) {
            showMessage("다운로드 실패", "APK 다운로드를 처리할 앱이 없습니다.");
        }
    }

    @SuppressWarnings("deprecation")
    private long currentVersionCode() {
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                return getPackageManager()
                        .getPackageInfo(getPackageName(), 0)
                        .getLongVersionCode();
            }
            return getPackageManager().getPackageInfo(getPackageName(), 0).versionCode;
        } catch (PackageManager.NameNotFoundException exception) {
            return 0;
        }
    }

    private String currentVersionName() {
        try {
            String version = getPackageManager()
                    .getPackageInfo(getPackageName(), 0)
                    .versionName;
            return version == null || version.trim().isEmpty() ? "-" : version;
        } catch (PackageManager.NameNotFoundException exception) {
            return "-";
        }
    }

    private void refresh() {
        refresh(false);
    }

    private void refresh(boolean allowCoreCache) {
        if (!screenActive || operationRunning) {
            return;
        }
        final int requestedTab = currentTab;
        final String token = AppConfig.getToken(prefs);
        if (token.isEmpty()) {
            cancelRefreshTask();
            if (!isFinishing() && !isDestroyed()) {
                showSettingsDialog();
            }
            return;
        }

        cancelRefreshTask();
        final String url = urlForTab(requestedTab);
        final String coreRequestKey = isCoreTab(requestedTab)
                ? RecentCoreSnapshotCache.requestKey(url, token)
                : "";
        if (allowCoreCache && isCoreTab(requestedTab)) {
            long nowElapsed = SystemClock.elapsedRealtime();
            RecentCoreSnapshotCache.Entry recent = RecentCoreSnapshotCache.getFresh(
                    coreRequestKey,
                    nowElapsed
            );
            JSONObject recentData = recent == null ? null : recent.data();
            if (recentData != null) {
                long completedAt = wallTimeForElapsed(recent.recordedAtElapsed);
                lastCoreRefreshCompletedAt = completedAt;
                lastHandledCoreObservationSequence = recent.observationSequence;
                lastCoreUnavailable = false;
                render(recentData, requestedTab);
                return;
            }
        }
        if (requestedTab == TAB_OPERATION && AppConfig.isPublicStatusEndpoint(prefs)) {
            render(new JSONObject(), requestedTab);
            return;
        }

        final long requestId = ++latestRequestId;
        final ApiClient.RequestCancellation cancellation = new ApiClient.RequestCancellation();
        refreshCancellation = cancellation;
        progress.setVisibility(View.VISIBLE);

        refreshTask = refreshExecutor.submit(() -> {
            if (!canExecuteRefresh(requestedTab, requestId)) {
                return;
            }
            long statusObservation = 0L;
            try {
                JSONObject data;
                long responseCompletedAtElapsed = 0L;
                if (isCoreTab(requestedTab)) {
                    if (allowCoreCache) {
                        RecentCoreSnapshotCache.Entry recent =
                                RecentCoreSnapshotCache.getFresh(
                                        coreRequestKey,
                                        SystemClock.elapsedRealtime()
                                );
                        JSONObject recentData = recent == null ? null : recent.data();
                        if (recentData != null) {
                            if (canExecuteRefresh(requestedTab, requestId)) {
                                mainHandler.post(() -> completeRefreshFromRecentCoreCache(
                                        coreRequestKey,
                                        requestedTab,
                                        requestId
                                ));
                            }
                            return;
                        }
                    }
                    statusObservation = MonitorStatusNotification.beginObservation();
                    CoreRequestCoordinator.Result result = CoreRequestCoordinator.getJson(
                            url,
                            token,
                            cancellation,
                            allowCoreCache
                    );
                    data = result.data();
                    responseCompletedAtElapsed = result.completedAtElapsed;
                } else {
                    data = ApiClient.getJson(url, token, cancellation);
                }
                if (!canExecuteRefresh(requestedTab, requestId)) {
                    return;
                }
                CoreStatusSnapshot coreStatus = null;
                if (isCoreTab(requestedTab)) {
                    coreStatus = validateCoreContract(data);
                }
                if (!canExecuteRefresh(requestedTab, requestId)) {
                    return;
                }
                CoreStatusSnapshot validatedCoreStatus = coreStatus;
                long validatedCompletedAtElapsed = responseCompletedAtElapsed;
                long completedStatusObservation = statusObservation;
                mainHandler.post(() -> completeRefresh(
                        data,
                        validatedCoreStatus,
                        requestedTab,
                        requestId,
                        completedStatusObservation,
                        coreRequestKey,
                        validatedCompletedAtElapsed
                ));
            } catch (Exception exception) {
                if (!canExecuteRefresh(requestedTab, requestId)) {
                    return;
                }
                long failedStatusObservation = statusObservation;
                mainHandler.post(() -> completeRefreshError(
                        exception,
                        requestedTab,
                        requestId,
                        failedStatusObservation,
                        coreRequestKey
                ));
            }
        });
    }

    private void completeRefreshFromRecentCoreCache(
            String coreRequestKey,
            int requestedTab,
            long requestId
    ) {
        if (!canRender(requestedTab, requestId)) {
            return;
        }
        RecentCoreSnapshotCache.Entry recent = RecentCoreSnapshotCache.getFresh(
                coreRequestKey,
                SystemClock.elapsedRealtime()
        );
        JSONObject data = recent == null ? null : recent.data();
        if (data == null) {
            refreshTask = null;
            refreshCancellation = null;
            refresh(true);
            return;
        }
        refreshTask = null;
        refreshCancellation = null;
        long completedAt = wallTimeForElapsed(recent.recordedAtElapsed);
        lastCoreRefreshCompletedAt = completedAt;
        lastHandledCoreObservationSequence = recent.observationSequence;
        lastCoreUnavailable = false;
        render(data, requestedTab);
    }

    private void completeRefresh(
            JSONObject data,
            CoreStatusSnapshot coreStatus,
            int requestedTab,
            long requestId,
            long statusObservation,
            String coreRequestKey,
            long responseCompletedAtElapsed
    ) {
        if (!canRender(requestedTab, requestId)) {
            return;
        }
        refreshTask = null;
        refreshCancellation = null;
        long completedAt = isCoreTab(requestedTab)
                ? wallTimeForElapsed(responseCompletedAtElapsed)
                : System.currentTimeMillis();
        if (isCoreTab(requestedTab)) {
            long recordedAtElapsed = responseCompletedAtElapsed > 0L
                    ? responseCompletedAtElapsed
                    : SystemClock.elapsedRealtime();
            boolean accepted = MonitorStatusNotification.showSummaryAndRunIfCurrent(
                    getApplicationContext(),
                    coreStatus,
                    statusObservation,
                    () -> RecentCoreSnapshotCache.record(
                            coreRequestKey,
                            data,
                            coreStatus,
                            statusObservation,
                            recordedAtElapsed
                    )
            );
            if (!accepted) {
                RecentCoreSnapshotCache.Entry recent = RecentCoreSnapshotCache.getFresh(
                        coreRequestKey,
                        SystemClock.elapsedRealtime()
                );
                JSONObject recentData = recent == null ? null : recent.data();
                if (recentData != null && recent.observationSequence > statusObservation) {
                    long recentCompletedAt = wallTimeForElapsed(recent.recordedAtElapsed);
                    lastCoreRefreshCompletedAt = recentCompletedAt;
                    lastHandledCoreObservationSequence = recent.observationSequence;
                    lastCoreUnavailable = false;
                    render(recentData, requestedTab);
                    return;
                }
                // A newer unavailable observation may own the notification, but this response is
                // still valid for the visible screen. Keep notification ordering and render it
                // only in this Activity instance.
                lastCoreRefreshCompletedAt = completedAt;
                lastHandledCoreObservationSequence = statusObservation;
                lastCoreUnavailable = false;
                render(data, requestedTab);
                return;
            }
            lastCoreRefreshCompletedAt = completedAt;
            lastHandledCoreObservationSequence = statusObservation;
            lastCoreUnavailable = false;
        }
        render(data, requestedTab);
    }

    private void completeRefreshError(
            Exception exception,
            int requestedTab,
            long requestId,
            long statusObservation,
            String coreRequestKey
    ) {
        if (!canRender(requestedTab, requestId)) {
            return;
        }
        refreshTask = null;
        refreshCancellation = null;
        long completedAt = System.currentTimeMillis();
        if (isCoreTab(requestedTab)) {
            boolean accepted = MonitorStatusNotification.showUnavailable(
                    getApplicationContext(),
                    ApiErrorMessage.from(exception),
                    statusObservation
            );
            if (!accepted) {
                RecentCoreSnapshotCache.Entry recent = RecentCoreSnapshotCache.getFresh(
                        coreRequestKey,
                        SystemClock.elapsedRealtime()
                );
                JSONObject recentData = recent == null ? null : recent.data();
                if (recentData != null && recent.observationSequence > statusObservation) {
                    long recentCompletedAt = wallTimeForElapsed(recent.recordedAtElapsed);
                    lastCoreRefreshCompletedAt = recentCompletedAt;
                    lastHandledCoreObservationSequence = recent.observationSequence;
                    lastCoreUnavailable = false;
                    render(recentData, requestedTab);
                    return;
                }
                lastCoreRefreshCompletedAt = completedAt;
                lastHandledCoreObservationSequence =
                        RecentCoreSnapshotCache.latestAcceptedObservationSequence();
                lastCoreUnavailable = true;
                renderLoadError(exception, requestedTab);
                return;
            }
        }
        if (isCoreTab(requestedTab)) {
            lastCoreRefreshCompletedAt = completedAt;
            lastHandledCoreObservationSequence = statusObservation;
            lastCoreUnavailable = true;
        }
        renderLoadError(exception, requestedTab);
    }

    private long wallTimeForElapsed(long completedAtElapsed) {
        long nowElapsed = SystemClock.elapsedRealtime();
        long nowWall = System.currentTimeMillis();
        if (completedAtElapsed <= 0L || nowElapsed < completedAtElapsed) {
            return nowWall;
        }
        return Math.max(1L, nowWall - (nowElapsed - completedAtElapsed));
    }

    private boolean canExecuteRefresh(int requestedTab, long requestId) {
        return !Thread.currentThread().isInterrupted()
                && canRender(requestedTab, requestId);
    }

    private boolean canRender(int requestedTab, long requestId) {
        return screenActive
                && !isDestroyed()
                && !isFinishing()
                && requestedTab == currentTab
                && requestId == latestRequestId;
    }

    private void cancelRefreshTask() {
        latestRequestId++;
        ApiClient.RequestCancellation cancellation = refreshCancellation;
        refreshCancellation = null;
        if (cancellation != null) {
            cancellation.cancel();
        }
        Future<?> task = refreshTask;
        refreshTask = null;
        if (task != null) {
            task.cancel(true);
        }
        if (progress != null) {
            progress.setVisibility(View.GONE);
        }
    }

    private ApiClient.RequestCancellation trackPauseRequest() {
        ApiClient.RequestCancellation cancellation = new ApiClient.RequestCancellation();
        pauseCancellations.add(cancellation);
        destroyCancellations.add(cancellation);
        return cancellation;
    }

    private ApiClient.RequestCancellation trackDestroyRequest() {
        ApiClient.RequestCancellation cancellation = new ApiClient.RequestCancellation();
        destroyCancellations.add(cancellation);
        return cancellation;
    }

    private void releaseRequest(ApiClient.RequestCancellation cancellation) {
        pauseCancellations.remove(cancellation);
        destroyCancellations.remove(cancellation);
    }

    private void cancelRequests(Set<ApiClient.RequestCancellation> cancellations) {
        for (ApiClient.RequestCancellation cancellation : cancellations) {
            cancellation.cancel();
        }
        cancellations.clear();
    }

    private void resetForegroundRefreshState() {
        cancelRefreshTask();
        RecentCoreSnapshotCache.clear();
        CoreRequestCoordinator.clear();
        lastCoreRefreshCompletedAt = 0L;
        lastHandledCoreObservationSequence = 0L;
        lastCoreUnavailable = false;
    }

    private boolean isCoreTab(int tab) {
        return tab == TAB_MOONCEN;
    }

    private boolean isAutoRefreshTab(int tab) {
        return tab == TAB_CRAWLER || tab == TAB_MOONCEN;
    }

    private String urlForTab(int tab) {
        if (tab == TAB_CRAWLER) {
            return AppConfig.crawlerUrl(prefs);
        }
        if (tab == TAB_MOONCEN) {
            return AppConfig.mooncenUrl(prefs);
        }
        if (tab == TAB_SERVER) {
            return AppConfig.serversUrl(prefs);
        }
        if (tab == TAB_OPERATION) {
            return AppConfig.operationsUrl(prefs);
        }
        throw new IllegalArgumentException("알 수 없는 화면입니다: " + tab);
    }

    private void render(JSONObject data, int tab) {
        progress.setVisibility(View.GONE);
        int previousScrollY = renderedTab == tab ? scrollView.getScrollY() : 0;
        content.removeAllViews();

        if (tab == TAB_CRAWLER) {
            renderCrawlerTab(data);
        } else if (tab == TAB_MOONCEN) {
            renderMooncenTab(data);
        } else if (tab == TAB_SERVER) {
            renderServersTab(data);
        } else {
            renderOperationTab(data);
        }

        String generatedAt = data.optString("generated_at", "");
        String updated = generatedAt.isEmpty()
                ? "앱 갱신: " + currentTime()
                : "데이터 갱신: " + formatTimestamp(generatedAt);
        updatedView.setText(getString(
                isAutoRefreshTab(tab)
                        ? R.string.updated_success_core
                        : R.string.updated_success_manual,
                updated
        ));
        updatedView.setVisibility(View.VISIBLE);
        renderedTab = tab;
        scrollView.post(() -> scrollView.scrollTo(0, previousScrollY));
    }

    private void renderCrawlerTab(JSONObject data) {
        CrawlerMonitoringSnapshot snapshot = CrawlerMonitoringSnapshot.parse(data);
        String topStatus = snapshot.contractValid
                ? CrawlerMonitoringPresentation.overallStatusLabel(snapshot.status)
                : getString(R.string.crawler_monitoring_unavailable);
        int topColor = snapshot.contractValid ? statusColor(snapshot.status) : COLOR_WARNING;
        String topDetail = !snapshot.contractValid
                ? "크롤러 모니터링 응답 형식을 확인할 수 없습니다."
                : snapshot.complete
                ? "최근 수집, 24시간 성과, Provider와 노드 증거가 모두 연결됐습니다."
                : snapshot.partial
                ? getString(R.string.crawler_monitoring_partial)
                : "사용 가능한 크롤러 수집 증거가 없습니다.";
        content.addView(statusMetricCard(
                "크롤러 수집 상태",
                topStatus,
                topDetail,
                topColor,
                new String[]{"최근 상태", "최근 완료", "Provider", "소요 시간"},
                new String[]{
                        snapshot.latest.available
                                ? CrawlerMonitoringPresentation.latestStatusLabel(
                                snapshot.latest.status
                        ) : "확인 불가",
                        !snapshot.latest.available || snapshot.latest.completedAt.isEmpty()
                                ? "확인 불가" : formatTimestamp(snapshot.latest.completedAt),
                        CrawlerMonitoringPresentation.count(
                                snapshot.latest.available
                                        ? snapshot.latest.providersRequested : null,
                                "개"
                        ),
                        CrawlerMonitoringPresentation.duration(
                                snapshot.latest.available
                                        ? snapshot.latest.durationSeconds : null
                        )
                },
                new int[]{
                        crawlerLatestStatusColor(
                                snapshot.latest.available ? snapshot.latest.status : "unknown"
                        ),
                        !snapshot.latest.available || snapshot.latest.completedAt.isEmpty()
                                ? COLOR_WARNING : COLOR_INFO,
                        !snapshot.latest.available || snapshot.latest.providersRequested == null
                                ? COLOR_WARNING : COLOR_INFO,
                        !snapshot.latest.available || snapshot.latest.durationSeconds == null
                                ? COLOR_WARNING : COLOR_INFO
                }
        ));
        renderCrawlerLatest(snapshot.latest, snapshot.errors);
        renderCrawlerPerformance(snapshot.summary24h, snapshot.errors);
        renderCrawlerQuality(snapshot.quality);
        renderCrawlerProviders(snapshot.providers, snapshot.errors);
        renderCrawlerNodes(snapshot);
        renderCrawlerMonitoringErrors(snapshot.errors);
    }

    private LinearLayout primaryStatusCard(CoreStatusSnapshot status) {
        String observedPrimary = displayNode(status.observedPrimaryNode());
        String expectedPrimary = displayNode(status.expectedPrimaryNode());
        StringBuilder primaryDetail = new StringBuilder()
                .append("관측: ").append(observedPrimary)
                .append("\n예상: ").append(expectedPrimary)
                .append("\nPrimary 역할: ")
                .append(StatusNotificationPresentation.booleanLabel(status.primary.roleOk))
                .append(" · DB 쓰기: ")
                .append(StatusNotificationPresentation.booleanLabel(status.primary.databaseWritable))
                .append(" · 토폴로지: ")
                .append(matchLabel(status.primary.matchesTopology));
        if (!status.primary.candidates.isEmpty()) {
            primaryDetail.append("\n관측 후보: ").append(String.join(", ", status.primary.candidates));
        }
        return statusCard(
                "Primary 서버",
                coreStateLabel(status.primary.state),
                primaryDetail.toString(),
                coreStateColor(status.primary.state)
        );
    }

    private void renderMooncenTab(JSONObject data) {
        CoreStatusSnapshot status = CoreStatusSnapshot.parse(data);
        CoreStatusSnapshot.State overallState = status.overallState();
        String[] metricLabels = new String[status.services.size()];
        String[] metricValues = new String[status.services.size()];
        int[] metricColors = new int[status.services.size()];
        for (int index = 0; index < status.services.size(); index++) {
            CoreStatusSnapshot.Service service = status.services.get(index);
            metricLabels[index] = CoreStatusSnapshot.serviceLabel(service.key);
            metricValues[index] = coreStateLabel(service.state);
            metricColors[index] = coreStateColor(service.state);
        }
        content.addView(statusMetricCard(
                "문센 핵심 상태",
                coreStateLabel(overallState),
                "문센 애플리케이션과 데이터 수집 상태입니다.\nPrimary "
                        + displayNode(status.observedPrimaryNode()),
                coreStateColor(overallState),
                metricLabels,
                metricValues,
                metricColors
        ));

        CoreStatusSnapshot.Service crawler = status.service(CoreStatusSnapshot.CRAWLER);
        JSONObject topology = data.optJSONObject("topology");
        CrawlerTopologyPlacement crawlerPlacement = CrawlerTopologyPlacement.parse(
                topology,
                crawler.primaryNode
        );
        content.addView(sectionHeading(
                "크롤러 구성",
                "현재 실행, 목표 워커와 중앙 배포·관리 위치를 구분합니다."
        ));
        renderCrawlerTopology(crawlerPlacement);

        content.addView(sectionHeading(
                "문센 서비스",
                "Primary와 네 핵심 서비스의 실행·기능 상태입니다."
        ));
        content.addView(primaryStatusCard(status));
        for (CoreStatusSnapshot.Service service : status.services) {
            content.addView(serviceStatusCard(service));
        }

        renderCrawlerSummary(data.optJSONObject("crawler"), crawler);
        content.addView(sectionHeading(
                "백업",
                "최근 예약 실행과 최신성 증거를 확인합니다."
        ));
        renderBackupCards(data.optJSONObject("backup"));
        renderErrors(data.optJSONArray("errors"));
    }

    private void renderCrawlerTopology(CrawlerTopologyPlacement placement) {
        if (!placement.valid) {
            content.addView(statusMetricCard(
                    "크롤러 토폴로지",
                    getString(R.string.crawler_transition_unknown),
                    getString(R.string.crawler_topology_unavailable),
                    COLOR_WARNING,
                    new String[]{"현재 실행", "목표 워커", "중앙 제어", "전환 상태", "배치 drift"},
                    new String[]{
                            "확인 불가",
                            "확인 불가",
                            "확인 불가",
                            getString(R.string.crawler_transition_unknown),
                            "확인 불가"
                    },
                    new int[]{COLOR_WARNING, COLOR_WARNING, COLOR_WARNING, COLOR_WARNING, COLOR_WARNING}
            ));
            return;
        }
        boolean pending = placement.transition
                == CrawlerTopologyPlacement.Transition.CUTOVER_PENDING;
        String transitionLabel = getString(
                pending
                        ? R.string.crawler_transition_cutover_pending
                        : R.string.crawler_transition_target_runtime
        );
        String driftLabel = getString(
                Boolean.TRUE.equals(placement.runtimeDrift)
                        ? R.string.crawler_runtime_drift_present
                        : R.string.crawler_runtime_drift_absent
        );
        int placementColor = pending ? COLOR_WARNING : COLOR_HEALTHY;
        String modeLabel = "legacy".equals(placement.mode) ? "레거시" : "분산";
        content.addView(statusMetricCard(
                "크롤러 토폴로지",
                transitionLabel,
                "운영 모드 " + modeLabel + " · 실행 위치와 목표 위치를 독립적으로 표시합니다.",
                placementColor,
                new String[]{"현재 실행", "목표 워커", "중앙 제어", "전환 상태", "배치 drift"},
                new String[]{
                        placement.runtimeNode,
                        placement.targetNode,
                        placement.controlNode,
                        transitionLabel,
                        driftLabel
                },
                new int[]{
                        placementColor,
                        COLOR_INFO,
                        COLOR_INFO,
                        placementColor,
                        placementColor
                }
        ));
    }

    private void renderCrawlerLatest(
            CrawlerMonitoringSnapshot.Latest latest,
            java.util.List<CrawlerMonitoringSnapshot.SectionError> errors
    ) {
        content.addView(sectionHeading(
                "최근 수집",
                "가장 최근에 확인된 크롤러 cycle 결과입니다."
        ));
        if (!latest.available) {
            content.addView(statusCard(
                    "최근 수집 결과",
                    "확인 불가",
                    crawlerUnavailableReason(errors, "latest"),
                    COLOR_WARNING
            ));
            return;
        }
        content.addView(statusMetricCard(
                "최근 수집 결과",
                CrawlerMonitoringPresentation.latestStatusLabel(latest.status),
                "증거 " + latest.source
                        + (latest.running ? " · oneshot 실행 중" : " · oneshot 대기")
                        + (latest.completedAt.isEmpty()
                        ? "" : " · 완료 " + formatTimestamp(latest.completedAt))
                        + (latest.lastSuccessAt.isEmpty()
                        ? "" : "\n마지막 성공 " + formatTimestamp(latest.lastSuccessAt)
                        + " · " + CrawlerMonitoringPresentation.age(
                        latest.lastSuccessAgeSeconds
                ) + " 전"),
                crawlerLatestStatusColor(latest.status),
                new String[]{"수집", "신규", "업데이트", "실패 Provider", "성공 Provider", "건너뜀"},
                new String[]{
                        CrawlerMonitoringPresentation.count(latest.collectedCount, "건"),
                        CrawlerMonitoringPresentation.count(latest.newCount, "건"),
                        CrawlerMonitoringPresentation.count(latest.updatedCount, "건"),
                        CrawlerMonitoringPresentation.count(latest.providersFailed, "개"),
                        CrawlerMonitoringPresentation.count(latest.providersSucceeded, "개"),
                        CrawlerMonitoringPresentation.count(latest.skippedCount, "건")
                },
                nullableMetricColors(
                        latest.collectedCount,
                        latest.newCount,
                        latest.updatedCount,
                        latest.providersFailed,
                        latest.providersSucceeded,
                        latest.skippedCount
                )
        ));
    }

    private void renderCrawlerPerformance(
            CrawlerMonitoringSnapshot.Summary24h summary,
            java.util.List<CrawlerMonitoringSnapshot.SectionError> errors
    ) {
        content.addView(sectionHeading(
                "24시간 성과",
                "실행 횟수, 수집·신규·업데이트와 평균 소요 시간입니다."
        ));
        if (!summary.available) {
            content.addView(statusCard(
                    "24시간 성과",
                    "확인 불가",
                    crawlerUnavailableReason(summary.reasons, errors, "summary_24h"),
                    COLOR_WARNING
            ));
            return;
        }
        if (!summary.hasData) {
            content.addView(statusCard(
                    "24시간 성과",
                    "데이터 없음",
                    "집계 원본은 연결됐지만 최근 24시간 실행 증거가 없습니다.",
                    COLOR_WARNING
            ));
            return;
        }
        content.addView(statusMetricCard(
                "24시간 성과",
                summary.failureCount != null && summary.failureCount > 0 ? "실패 있음" : "집계됨",
                "증거 " + summary.source
                        + (summary.lastRunAt.isEmpty()
                        ? "" : " · 마지막 실행 " + formatTimestamp(summary.lastRunAt)),
                summary.failureCount != null && summary.failureCount > 0
                        ? COLOR_WARNING : COLOR_HEALTHY,
                new String[]{"실행", "성공", "부분 성공", "실패", "진행 중", "수집", "처리", "신규", "업데이트", "건너뜀", "평균 시간"},
                new String[]{
                        CrawlerMonitoringPresentation.count(summary.runCount, "회"),
                        CrawlerMonitoringPresentation.count(summary.successCount, "회"),
                        CrawlerMonitoringPresentation.count(summary.partialCount, "회"),
                        CrawlerMonitoringPresentation.count(summary.failureCount, "회"),
                        CrawlerMonitoringPresentation.count(summary.inProgressCount, "회"),
                        CrawlerMonitoringPresentation.count(summary.collectedCount, "건"),
                        CrawlerMonitoringPresentation.count(summary.processedCount, "건"),
                        CrawlerMonitoringPresentation.count(summary.newCount, "건"),
                        CrawlerMonitoringPresentation.count(summary.updatedCount, "건"),
                        CrawlerMonitoringPresentation.count(summary.skippedCount, "건"),
                        CrawlerMonitoringPresentation.duration(summary.averageDurationSeconds)
                },
                nullableMetricColors(
                        summary.runCount,
                        summary.successCount,
                        summary.partialCount,
                        summary.failureCount,
                        summary.inProgressCount,
                        summary.collectedCount,
                        summary.processedCount,
                        summary.newCount,
                        summary.updatedCount,
                        summary.skippedCount,
                        summary.averageDurationSeconds
                )
        ));
    }

    private void renderCrawlerProviders(
            CrawlerMonitoringSnapshot.Providers providers,
            java.util.List<CrawlerMonitoringSnapshot.SectionError> errors
    ) {
        content.addView(sectionHeading(
                "Provider 성과",
                "최근 24시간 Provider별 실행과 성공률입니다."
        ));
        if (!providers.available) {
            content.addView(statusCard(
                    "Provider 성과",
                    "확인 불가",
                    crawlerUnavailableReason(providers.reasons, errors, "providers"),
                    COLOR_WARNING
            ));
            return;
        }
        if (!providers.hasData || providers.items.isEmpty()) {
            content.addView(statusCard(
                    "Provider 성과",
                    "데이터 없음",
                    "최근 24시간 Provider 실행 증거가 없습니다.",
                    COLOR_WARNING
            ));
            return;
        }
        for (CrawlerMonitoringSnapshot.Provider provider : providers.items) {
            content.addView(statusMetricCard(
                    provider.provider,
                    provider.failureCount != null && provider.failureCount > 0
                            ? "실패 있음" : "집계됨",
                    provider.lastRunAt.isEmpty()
                            ? "마지막 실행 확인 불가"
                            : "마지막 실행 " + formatTimestamp(provider.lastRunAt),
                    provider.failureCount != null && provider.failureCount > 0
                            ? COLOR_WARNING : COLOR_HEALTHY,
                    new String[]{"실행", "성공", "부분 성공", "실패", "수집", "신규", "업데이트", "항목 실패", "성공률"},
                    new String[]{
                            CrawlerMonitoringPresentation.count(provider.runCount, "회"),
                            CrawlerMonitoringPresentation.count(provider.successCount, "회"),
                            CrawlerMonitoringPresentation.count(provider.partialCount, "회"),
                            CrawlerMonitoringPresentation.count(provider.failureCount, "회"),
                            CrawlerMonitoringPresentation.count(provider.collectedCount, "건"),
                            CrawlerMonitoringPresentation.count(provider.newCount, "건"),
                            CrawlerMonitoringPresentation.count(provider.updatedCount, "건"),
                            CrawlerMonitoringPresentation.count(provider.failedItemCount, "건"),
                            CrawlerMonitoringPresentation.percentage(provider.successRate)
                    },
                    nullableMetricColors(
                            provider.runCount,
                            provider.successCount,
                            provider.partialCount,
                            provider.failureCount,
                            provider.collectedCount,
                            provider.newCount,
                            provider.updatedCount,
                            provider.failedItemCount,
                            provider.successRate
                    )
            ));
        }
        if (providers.truncated) {
            content.addView(statusCard(
                    "Provider 목록",
                    "일부 표시",
                    "전체 " + CrawlerMonitoringPresentation.count(providers.total, "개")
                            + " 중 상위 " + providers.items.size() + "개를 표시합니다.",
                    COLOR_INFO
            ));
        }
    }

    private void renderCrawlerQuality(CrawlerMonitoringSnapshot.Quality quality) {
        content.addView(sectionHeading(
                "생산 데이터 품질",
                "운영 사이트에 노출되는 활성 강좌의 품질 근거입니다."
        ));
        if (!quality.available) {
            content.addView(statusCard(
                    "생산 데이터 품질",
                    "확인 불가",
                    crawlerQualityUnavailableReason(quality),
                    COLOR_WARNING
            ));
            return;
        }
        boolean hasIssues = quality.missingRequired > 0
                || quality.invalidDates > 0
                || quality.invalidPrices > 0
                || quality.incompleteLocation > 0
                || quality.outOfKorea > 0
                || quality.duplicateUrls > 0
                || quality.blockedSync > 0;
        boolean hasActiveIssueStatusEvidence = false;
        for (CrawlerMonitoringSnapshot.IssueStatus issueStatus : quality.issueStatuses) {
            boolean activeStatus = "open".equals(issueStatus.status)
                    || "reviewing".equals(issueStatus.status);
            hasActiveIssueStatusEvidence = hasActiveIssueStatusEvidence
                    || (activeStatus && issueStatus.issueCount > 0);
        }
        hasIssues = hasIssues || hasActiveIssueStatusEvidence;
        String latestScan = quality.latestScanAt.isEmpty()
                ? "최근 품질 스캔 확인 불가"
                : "최근 품질 스캔 " + formatTimestamp(quality.latestScanAt);
        String hiddenIssueDetail = quality.outOfKorea > 0
                ? " · 국내 범위 밖 좌표 " + quality.outOfKorea + "건"
                : "";
        if (hasActiveIssueStatusEvidence) {
            hiddenIssueDetail += " · 미해결 품질 이슈 기록 있음";
        }
        content.addView(statusMetricCard(
                "생산 데이터 품질",
                hasIssues ? "품질 이슈 있음" : "품질 이슈 없음",
                latestScan + " · 근거 " + quality.ruleSource + hiddenIssueDetail,
                hasIssues ? COLOR_WARNING : COLOR_HEALTHY,
                new String[]{
                        "활성 강좌",
                        "필수값 누락",
                        "날짜 오류",
                        "가격 오류",
                        "위치 불완전",
                        "중복 URL",
                        "동기화 차단"
                },
                new String[]{
                        CrawlerMonitoringPresentation.count(quality.activeCourses, "건"),
                        CrawlerMonitoringPresentation.count(quality.missingRequired, "건"),
                        CrawlerMonitoringPresentation.count(quality.invalidDates, "건"),
                        CrawlerMonitoringPresentation.count(quality.invalidPrices, "건"),
                        CrawlerMonitoringPresentation.count(quality.incompleteLocation, "건"),
                        CrawlerMonitoringPresentation.count(quality.duplicateUrls, "건"),
                        CrawlerMonitoringPresentation.count(quality.blockedSync, "건")
                },
                new int[]{
                        COLOR_INFO,
                        crawlerQualityMetricColor(quality.missingRequired, false),
                        crawlerQualityMetricColor(quality.invalidDates, false),
                        crawlerQualityMetricColor(quality.invalidPrices, false),
                        crawlerQualityMetricColor(quality.incompleteLocation, false),
                        crawlerQualityMetricColor(quality.duplicateUrls, false),
                        crawlerQualityMetricColor(quality.blockedSync, true)
                }
        ));
    }

    private String crawlerQualityUnavailableReason(CrawlerMonitoringSnapshot.Quality quality) {
        if (!quality.present) {
            return "현재 schema v1 응답에는 생산 품질 요약이 없습니다. 서버 갱신 상태를 확인하세요.";
        }
        if (!quality.contractValid) {
            return "생산 품질 응답 형식이 올바르지 않아 수치를 표시하지 않습니다.";
        }
        if ("server_monitor_token_not_configured".equals(quality.reasonCode)) {
            return "모니터 서버의 생산 품질 조회 토큰이 설정되지 않았습니다.";
        }
        if ("server_monitor_token_invalid".equals(quality.reasonCode)) {
            return "모니터 서버의 생산 품질 조회 토큰 형식이 올바르지 않습니다.";
        }
        if ("server_monitor_base_url_invalid".equals(quality.reasonCode)) {
            return "생산 품질 조회 주소 설정을 확인할 수 없습니다.";
        }
        if ("server_monitor_request_failed".equals(quality.reasonCode)) {
            return "생산 품질 조회 서버에 연결하지 못했습니다.";
        }
        if ("server_monitor_response_invalid".equals(quality.reasonCode)) {
            return "생산 품질 조회 응답을 검증하지 못했습니다.";
        }
        if ("quality_refresh_pending".equals(quality.reasonCode)) {
            return "생산 품질 수치를 갱신하고 있습니다. 잠시 후 새로고침해 주세요.";
        }
        if ("quality_refresh_start_failed".equals(quality.reasonCode)
                || "collector_unavailable".equals(quality.reasonCode)
                || "production_quality_unavailable".equals(quality.reasonCode)) {
            return "생산 품질 수치를 현재 확인할 수 없습니다. 잠시 후 다시 시도해 주세요.";
        }
        return "생산 품질 수치를 현재 확인할 수 없습니다.";
    }

    private int crawlerQualityMetricColor(Long value, boolean criticalWhenPositive) {
        if (value == null) {
            return COLOR_WARNING;
        }
        if (value == 0) {
            return COLOR_HEALTHY;
        }
        return criticalWhenPositive ? COLOR_CRITICAL : COLOR_WARNING;
    }

    private void renderCrawlerNodes(CrawlerMonitoringSnapshot snapshot) {
        content.addView(sectionHeading(
                "크롤러 노드",
                "현재 실행·목표 워커·중앙 제어 노드의 자원과 온도입니다."
        ));
        for (String role : new String[]{"runtime", "target", "control"}) {
            CrawlerMonitoringSnapshot.Node node = snapshot.node(role);
            String roleLabel = CrawlerMonitoringPresentation.nodeRoleLabel(role);
            if (!node.valid) {
                content.addView(statusCard(
                        roleLabel,
                        "확인 불가",
                        "노드 상태 응답이 없거나 형식이 올바르지 않습니다.",
                        COLOR_WARNING
                ));
                continue;
            }
            int nodeColor = !node.available || "unknown".equals(node.status)
                    ? COLOR_WARNING
                    : "down".equals(node.status) ? COLOR_CRITICAL : COLOR_HEALTHY;
            String detail = "역할 " + roleLabel;
            if (!node.error.isEmpty()) {
                detail += " · " + node.error;
            }
            content.addView(statusMetricCard(
                    node.node,
                    CrawlerMonitoringPresentation.nodeStatusLabel(node),
                    detail,
                    nodeColor,
                    new String[]{"CPU", "메모리", "1분 부하", "디스크", "논리 CPU", "온도"},
                    new String[]{
                            node.available
                                    ? CrawlerMonitoringPresentation.percentage(node.cpuPercent)
                                    : "확인 불가",
                            node.available
                                    ? CrawlerMonitoringPresentation.percentage(node.memoryPercent)
                                    : "확인 불가",
                            node.available
                                    ? CrawlerMonitoringPresentation.load(node.load1m)
                                    : "확인 불가",
                            node.available
                                    ? CrawlerMonitoringPresentation.percentage(node.diskPercent)
                                    : "확인 불가",
                            node.available
                                    ? CrawlerMonitoringPresentation.count(
                                    node.logicalCpuCount,
                                    "개"
                            ) : "확인 불가",
                            CrawlerMonitoringPresentation.nodeTemperatureLabel(node)
                    },
                    nullableMetricColors(
                            node.available ? node.cpuPercent : null,
                            node.available ? node.memoryPercent : null,
                            node.available ? node.load1m : null,
                            node.available ? node.diskPercent : null,
                            node.available ? node.logicalCpuCount : null,
                            node.available && node.temperatureAvailable
                                    ? node.temperatureCelsius : null
                    )
            ));
        }
    }

    private void renderCrawlerMonitoringErrors(
            java.util.List<CrawlerMonitoringSnapshot.SectionError> errors
    ) {
        if (errors.isEmpty()) {
            return;
        }
        StringBuilder detail = new StringBuilder();
        for (CrawlerMonitoringSnapshot.SectionError error : errors) {
            if (detail.length() > 0) {
                detail.append('\n');
            }
            detail.append("• ").append(error.section).append(" · ").append(error.code);
        }
        content.addView(statusCard(
                "부분 수집 오류",
                "확인 필요",
                detail.toString(),
                COLOR_WARNING
        ));
    }

    private String crawlerUnavailableReason(
            java.util.List<CrawlerMonitoringSnapshot.SectionError> errors,
            String section
    ) {
        for (CrawlerMonitoringSnapshot.SectionError error : errors) {
            if (section.equals(error.section)) {
                return "사용 불가 사유: " + error.code;
            }
        }
        return "사용 가능한 수집 증거가 없습니다.";
    }

    private String crawlerUnavailableReason(
            java.util.List<String> reasons,
            java.util.List<CrawlerMonitoringSnapshot.SectionError> errors,
            String section
    ) {
        if (reasons != null && !reasons.isEmpty()) {
            return "사용 불가 사유: " + TextUtils.join(", ", reasons);
        }
        return crawlerUnavailableReason(errors, section);
    }

    private int[] nullableMetricColors(Object... values) {
        int[] colors = new int[values.length];
        for (int index = 0; index < values.length; index++) {
            colors[index] = values[index] == null ? COLOR_WARNING : COLOR_INFO;
        }
        return colors;
    }

    private void renderCrawlerSummary(
            JSONObject crawlerSummary,
            CoreStatusSnapshot.Service crawlerService
    ) {
        content.addView(sectionHeading(
                "크롤러 수집",
                "최근 실행 결과와 24시간 집계입니다."
        ));
        boolean available = crawlerSummary != null
                && crawlerSummary.optBoolean("available", false);
        String status = crawlerSummary == null
                ? "unknown"
                : crawlerSummary.optString("status", "unknown");
        String detail = crawlerSummary == null
                ? crawlerService.detail
                : firstNonEmpty(crawlerSummary.optString("detail", ""), crawlerService.detail);
        if (!available) {
            content.addView(statusCard(
                    "크롤러 실행 결과",
                    "확인 필요",
                    firstNonEmpty(detail, "최근 실행 결과를 확인할 수 없습니다."),
                    COLOR_WARNING
            ));
            return;
        }
        content.addView(statusMetricCard(
                "크롤러 실행 결과",
                statusLabel(status),
                firstNonEmpty(detail, "최근 실행 상세가 없습니다."),
                statusColor(status),
                new String[]{"24시간 성공", "24시간 실패", "수집"},
                new String[]{
                        crawlerSummary.optInt("success_24h", 0) + "회",
                        crawlerSummary.optInt("failed_24h", 0) + "회",
                        crawlerSummary.optInt("collected_24h", 0) + "건"
                },
                new int[]{
                        COLOR_HEALTHY,
                        crawlerSummary.optInt("failed_24h", 0) == 0
                                ? COLOR_HEALTHY : COLOR_CRITICAL,
                        COLOR_INFO
                }
        ));
        JSONArray failures = crawlerSummary.optJSONArray("latest_failures");
        if (failures != null && failures.length() > 0) {
            content.addView(statusCard(
                    "최근 크롤러 실패",
                    "확인 필요",
                    renderFailures(failures),
                    COLOR_CRITICAL
            ));
        }
    }

    private LinearLayout serviceStatusCard(CoreStatusSnapshot.Service service) {
        String activeNodes = service.activeNodes.isEmpty()
                ? "확인 불가"
                : String.join(", ", service.activeNodes);
        StringBuilder detail = new StringBuilder()
                .append("Primary: ").append(displayNode(service.primaryNode))
                .append("\n동작 노드: ").append(activeNodes)
                .append("\n실행: ")
                .append(StatusNotificationPresentation.booleanLabel(service.runtimeOk))
                .append(" · 실제 기능: ")
                .append(StatusNotificationPresentation.booleanLabel(service.functionalOk));
        if (!service.checkedAt.isEmpty()) {
            detail.append("\n확인: ").append(formatTimestamp(service.checkedAt));
        }
        if (!service.detail.isEmpty()) {
            detail.append("\n").append(service.detail);
        }
        return statusCard(
                CoreStatusSnapshot.serviceLabel(service.key),
                coreStateLabel(service.state),
                detail.toString(),
                coreStateColor(service.state)
        );
    }

    private void renderServersTab(JSONObject data) {
        JSONArray rows = data.optJSONArray("servers");
        if (rows == null || rows.length() == 0) {
            content.addView(statusCard(
                    "서버",
                    "정보 없음",
                    "서버 정보가 없습니다.",
                    COLOR_WARNING
            ));
            renderErrors(data.optJSONArray("errors"));
            return;
        }
        int down = 0;
        for (int i = 0; i < rows.length(); i++) {
            JSONObject row = rows.optJSONObject(i);
            if (row != null && !"UP".equalsIgnoreCase(row.optString("up", ""))) {
                down++;
            }
        }
        content.addView(statusMetricCard(
                "서버 상태",
                down == 0 ? "정상" : "장애",
                "노드 가용성",
                down == 0 ? COLOR_HEALTHY : COLOR_CRITICAL,
                new String[]{"전체", "온라인", "오프라인"},
                new String[]{
                        rows.length() + "대",
                        (rows.length() - down) + "대",
                        down + "대"
                },
                new int[]{
                        COLOR_INFO,
                        COLOR_HEALTHY,
                        down == 0 ? COLOR_HEALTHY : COLOR_CRITICAL
                }
        ));
        for (int i = 0; i < rows.length(); i++) {
            JSONObject row = rows.optJSONObject(i);
            if (row == null) {
                continue;
            }
            boolean up = "UP".equalsIgnoreCase(row.optString("up", ""));
            String detail = "온도 " + row.optString("temp", "-")
                    + " · 가동 " + row.optString("uptime", "-")
                    + "\n역할 " + MonitorPresentation.roleLabel(
                            row.optString("role", "-")
                    );
            content.addView(statusMetricCard(
                    row.optString("node", "-"),
                    up ? "온라인" : "오프라인",
                    detail,
                    up ? COLOR_HEALTHY : COLOR_CRITICAL,
                    new String[]{"CPU", "메모리", "디스크"},
                    new String[]{
                            row.optString("cpu", "-"),
                            row.optString("mem", "-"),
                            row.optString("disk", "-")
                    },
                    new int[]{
                            up ? COLOR_INFO : COLOR_CRITICAL,
                            up ? COLOR_INFO : COLOR_CRITICAL,
                            up ? COLOR_INFO : COLOR_CRITICAL
                    }
            ));
        }
        renderErrors(data.optJSONArray("errors"));
    }

    private void renderTailscaleTab(JSONObject data) {
        boolean available = data.optBoolean("available", false);
        JSONObject summary = data.optJSONObject("summary");
        if (summary == null) {
            summary = data.optJSONObject("counts");
        }
        JSONArray peers = data.optJSONArray("peers");
        int online = count(summary, "online");
        int offline = count(summary, "offline");
        int total = count(summary, "total");
        int direct = count(summary, "direct");
        int relay = count(summary, "relay");
        String backendState = data.optString("backend_state", "").trim();
        boolean stale = data.optBoolean("stale", false);

        if (!available) {
            content.addView(statusCard(
                    "Tailscale",
                    "사용 불가",
                    MonitorPresentation.tailscaleErrorMessage(
                            data.optString("error", "")
                    ),
                    COLOR_CRITICAL
            ));
            renderErrors(data.optJSONArray("errors"));
            return;
        }

        boolean hasConnectionSummary = summary != null
                && summary.has("direct")
                && summary.has("relay");
        String[] metricLabels = hasConnectionSummary
                ? new String[]{"온라인", "오프라인", "전체", "직접 연결", "릴레이"}
                : new String[]{"온라인", "오프라인", "전체"};
        String[] metricValues = hasConnectionSummary
                ? new String[]{
                        online + "대",
                        offline + "대",
                        total + "대",
                        direct + "대",
                        relay + "대"
                }
                : new String[]{online + "대", offline + "대", total + "대"};
        int[] metricColors = hasConnectionSummary
                ? new int[]{
                        COLOR_HEALTHY,
                        offline == 0 ? COLOR_HEALTHY : COLOR_CRITICAL,
                        COLOR_INFO,
                        COLOR_HEALTHY,
                        relay == 0 ? COLOR_HEALTHY : COLOR_WARNING
                }
                : new int[]{
                        COLOR_HEALTHY,
                        offline == 0 ? COLOR_HEALTHY : COLOR_CRITICAL,
                        COLOR_INFO
                };
        int tailnetColor = stale
                ? COLOR_WARNING
                : offline == 0 ? COLOR_HEALTHY : COLOR_WARNING;
        String snapshotAge = data.has("age_seconds") && !data.isNull("age_seconds")
                ? " · 스냅샷 " + formatAge(data.optLong("age_seconds", 0L)) + " 전"
                : "";
        String staleMessage = stale
                ? "\n" + MonitorPresentation.tailscaleErrorMessage(
                        data.optString("error", "snapshot_stale")
                )
                : "";
        content.addView(statusMetricCard(
                "Tailscale 네트워크",
                stale ? "데이터 지연" : offline == 0 ? "정상" : "확인 필요",
                "Online 여부를 기준으로 기기 상태를 표시합니다."
                        + (backendState.isEmpty() ? "" : " · 백엔드 " + backendState)
                        + snapshotAge
                        + staleMessage,
                tailnetColor,
                metricLabels,
                metricValues,
                metricColors
        ));

        JSONObject self = data.optJSONObject("self");
        if (self == null) {
            content.addView(statusCard(
                    "내 기기",
                    "정보 없음",
                    "로컬 Tailscale 기기 정보가 없습니다.",
                    COLOR_WARNING
            ));
        } else {
            content.addView(tailscaleDeviceCard(self, true));
        }

        if (peers == null || peers.length() == 0) {
            content.addView(statusCard(
                    "피어",
                    "0대",
                    "표시할 Tailscale 피어가 없습니다.",
                    COLOR_MUTED
            ));
        } else {
            content.addView(section(
                    "Tailscale 피어",
                    "총 " + peers.length() + "대 · Online 기준",
                    COLOR_INFO
            ));
            for (int i = 0; i < peers.length(); i++) {
                JSONObject peer = peers.optJSONObject(i);
                if (peer != null) {
                    content.addView(tailscaleDeviceCard(peer, false));
                }
            }
        }
        renderErrors(data.optJSONArray("errors"));
    }

    private View tailscaleDeviceCard(JSONObject device, boolean self) {
        boolean online = device.optBoolean("online", false);
        boolean active = device.optBoolean("active", false);
        int level = MonitorPresentation.peerLevel(online, active);
        int color = presentationColor(level);
        String rawName = device.optString("name", "").trim();
        String dnsName = device.optString("dns_name", "").trim();
        String name = MonitorPresentation.tailscaleDisplayName(
                dnsName,
                rawName,
                self ? "내 기기" : "이름 없는 피어"
        );
        String ip = readableValue(device.has("ip") ? device.opt("ip") : device.opt("ips"));
        String os = device.optString("os", "-").trim();
        String rawConnection = device.optString("connection", "").trim();
        String connection = rawConnection.isEmpty()
                ? ""
                : MonitorPresentation.connectionLabel(rawConnection);
        String backendState = device.optString("backend_state", "").trim();
        String lastSeen = formatOptionalTimestamp(device.optString("last_seen", ""));
        String keyExpiry = formatOptionalTimestamp(device.optString("key_expiry", ""));

        StringBuilder detail = new StringBuilder();
        if (!rawName.isEmpty() && !rawName.equalsIgnoreCase(name)) {
            detail.append("노드 ").append(rawName).append('\n');
        }
        if (!dnsName.isEmpty() && !dnsName.equalsIgnoreCase(name)) {
            detail.append("DNS ").append(dnsName).append('\n');
        }
        if (!ip.isEmpty()) {
            detail.append("IP ").append(ip).append('\n');
        }
        detail.append("OS ").append(os.isEmpty() ? "-" : os);
        if (!connection.isEmpty()) {
            detail.append(" · 연결 ").append(connection);
        }
        if (!backendState.isEmpty()) {
            detail.append(" · 백엔드 ").append(backendState);
        }
        if (device.has("active")) {
            detail.append(" · 최근 활동 ").append(active ? "있음" : "없음");
        }
        detail.append('\n')
                .append("마지막 확인 ")
                .append(online && "-".equals(lastSeen) ? "현재 온라인" : lastSeen);
        if (!"-".equals(keyExpiry)) {
            detail.append("\n키 만료 ").append(keyExpiry);
        }

        return statusCard(
                (self ? "내 기기 · " : "") + name,
                MonitorPresentation.peerBadge(online, active),
                detail.toString(),
                color
        );
    }

    private void renderOperationTab(JSONObject data) {
        if (AppConfig.isPublicStatusEndpoint(prefs)) {
            content.addView(statusCard(
                    "원격 작업 비활성",
                    "조회 전용",
                    "인터넷 연결은 상태 조회 전용입니다.\n원격 작업은 비활성화되어 있습니다.",
                    COLOR_WARNING
            ));
            return;
        }

        boolean serverConfigured = data.optBoolean("token_configured", false);
        boolean appConfigured = !AppConfig.getToken(prefs).isEmpty();
        String securityText = "서버 토큰: " + (serverConfigured ? "설정됨" : "설정되지 않음")
                + "\n앱 토큰: " + (appConfigured ? "입력됨" : "입력 필요");
        content.addView(statusCard(
                "작업 보안",
                serverConfigured && appConfigured ? "준비됨" : "설정 필요",
                securityText,
                serverConfigured && appConfigured ? COLOR_HEALTHY : COLOR_WARNING
        ));

        JSONArray actions = data.optJSONArray("actions");
        if (actions == null || actions.length() == 0) {
            content.addView(statusCard(
                    "원격 작업",
                    "없음",
                    "실행 가능한 작업이 없습니다.",
                    COLOR_MUTED
            ));
            return;
        }
        for (int i = 0; i < actions.length(); i++) {
            JSONObject action = actions.optJSONObject(i);
            if (action == null) {
                continue;
            }
            content.addView(actionButton(
                    action.optString("label", "-"),
                    action.optString("id", ""),
                    action.optString("kind", ""),
                    action.optString("node", "")
            ));
        }
    }

    private View actionButton(String label, String actionId, String kind, String node) {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setGravity(Gravity.CENTER_VERTICAL);
        box.setPadding(dp(14), dp(12), dp(14), dp(12));
        box.setBackground(roundedBackground(COLOR_SURFACE_ALT, COLOR_BORDER, 12));
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        );
        params.setMargins(0, 0, 0, dp(10));
        box.setLayoutParams(params);

        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.HORIZONTAL);
        header.setGravity(Gravity.CENTER_VERTICAL);
        TextView title = bodyText(label);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        header.addView(title, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
        header.addView(statusBadge("실행", COLOR_INFO));
        TextView meta = smallText((node.isEmpty() ? "-" : node) + " · " + operationKindLabel(kind));
        meta.setPadding(0, dp(5), 0, 0);
        box.addView(header);
        box.addView(meta);
        box.setContentDescription(label + " 실행");
        box.setClickable(true);
        box.setFocusable(true);
        box.setOnClickListener(v -> confirmRun(label, actionId));
        return box;
    }

    private void confirmRun(String label, String actionId) {
        if (actionId.trim().isEmpty()) {
            showMessage("실행 불가", "작업 식별자가 없습니다.");
            return;
        }
        new AlertDialog.Builder(this)
                .setTitle("작업 실행")
                .setMessage(label + "\n\n이 작업을 실행할까요?")
                .setPositiveButton("실행", (dialog, which) -> runAction(actionId))
                .setNegativeButton("취소", null)
                .show();
    }

    private void runAction(String actionId) {
        if (operationRunning) {
            return;
        }
        if (AppConfig.isPublicStatusEndpoint(prefs)) {
            showMessage(
                    "원격 작업 비활성",
                    "인터넷 연결은 상태 조회 전용입니다.\n원격 작업은 비활성화되어 있습니다."
            );
            refresh();
            return;
        }
        final String runBaseUrl = AppConfig.getBaseUrl(prefs);
        final String runToken = AppConfig.getToken(prefs);
        final String runUrl = AppConfig.runOperationUrl(prefs);
        final String runFingerprint = connectionFingerprint(runBaseUrl, runToken);
        cancelRefreshTask();
        operationRunning = true;
        progress.setVisibility(View.VISIBLE);
        setEnabledRecursive(content, false);
        ApiClient.RequestCancellation cancellation = trackDestroyRequest();
        executor.execute(() -> {
            try {
                JSONObject payload = new JSONObject();
                payload.put("action", actionId);
                JSONObject result = ApiClient.postJson(
                        runUrl,
                        runToken,
                        payload,
                        cancellation
                );
                mainHandler.post(() -> finishAction(result, null, runFingerprint));
            } catch (Exception exception) {
                mainHandler.post(() -> finishAction(null, exception, runFingerprint));
            } finally {
                releaseRequest(cancellation);
            }
        });
    }

    private void finishAction(
            JSONObject result,
            Exception exception,
            String runFingerprint
    ) {
        if (isDestroyed()) {
            return;
        }
        operationRunning = false;
        progress.setVisibility(View.GONE);
        setEnabledRecursive(content, true);
        if (!screenActive || isFinishing()) {
            operationResultPending = true;
            pendingOperationResult = result;
            pendingOperationException = exception;
            pendingOperationFingerprint = runFingerprint;
            return;
        }

        presentOperationResult(result, exception, runFingerprint);
    }

    private boolean deliverPendingOperationResult() {
        if (!operationResultPending) {
            return false;
        }
        JSONObject result = pendingOperationResult;
        Exception exception = pendingOperationException;
        String fingerprint = pendingOperationFingerprint;
        operationResultPending = false;
        pendingOperationResult = null;
        pendingOperationException = null;
        pendingOperationFingerprint = null;
        presentOperationResult(result, exception, fingerprint);
        return true;
    }

    private void presentOperationResult(
            JSONObject result,
            Exception exception,
            String runFingerprint
    ) {

        String currentFingerprint = connectionFingerprint(
                AppConfig.getBaseUrl(prefs),
                AppConfig.getToken(prefs)
        );
        if (!runFingerprint.equals(currentFingerprint)) {
            showMessage("작업 결과 무시", "연결 설정이 변경되어 이전 작업 결과를 표시하지 않습니다.");
            refresh();
            restartAutoRefreshTimer();
            return;
        }

        if (exception != null) {
            showMessage("작업 실패", ApiErrorMessage.from(exception));
        } else {
            boolean ok = result != null && result.optBoolean("ok", false);
            String output = result == null ? "" :
                    (result.optString("stdout", "") + "\n" + result.optString("stderr", "")).trim();
            if (output.isEmpty() && result != null) {
                output = result.optString("error", "");
            }
            if (output.isEmpty()) {
                output = ok ? "작업이 완료되었습니다." : "작업이 실패했습니다.";
            }
            showMessage(ok ? "작업 완료" : "작업 실패", output);
        }
        refresh();
        restartAutoRefreshTimer();
    }

    private void renderLoadError(Exception exception, int tab) {
        progress.setVisibility(View.GONE);
        content.removeAllViews();
        String message = ApiErrorMessage.from(exception);
        String baseUrl = AppConfig.getBaseUrl(prefs);
        if (!baseUrl.toLowerCase(Locale.ROOT).startsWith("https://")) {
            message += "\n\n기존 내부 주소는 사용할 수 없습니다. 공개 HTTPS API 주소로 변경하세요.";
        }
        content.addView(section("연결 실패", message, COLOR_CRITICAL));
        content.addView(section(
                "접속 정보",
                baseUrl + "\n설정의 연결 테스트로 주소와 토큰을 확인할 수 있습니다.",
                COLOR_WARNING
        ));
        content.addView(settingsActionButton());
        updatedView.setText(getString(
                isAutoRefreshTab(tab)
                        ? R.string.updated_failed_core
                        : R.string.updated_failed_manual,
                currentTime()
        ));
        updatedView.setVisibility(View.VISIBLE);
        renderedTab = tab;
        scrollView.scrollTo(0, 0);
    }

    private void renderErrors(JSONArray errors) {
        if (errors == null || errors.length() == 0) {
            return;
        }
        StringBuilder body = new StringBuilder();
        for (int i = 0; i < errors.length(); i++) {
            String error = errors.optString(i, "").trim();
            if (error.isEmpty()) {
                continue;
            }
            if (body.length() > 0) {
                body.append('\n');
            }
            body.append("• ").append(error);
        }
        if (body.length() > 0) {
            content.addView(statusCard(
                    "부분 수집 오류",
                    "확인 필요",
                    body.toString(),
                    COLOR_CRITICAL
            ));
        }
    }

    private LinearLayout sectionHeading(String titleText, String subtitleText) {
        LinearLayout heading = new LinearLayout(this);
        heading.setOrientation(LinearLayout.VERTICAL);
        heading.setPadding(dp(2), dp(8), dp(2), dp(10));

        TextView title = smallText(titleText);
        title.setTextColor(COLOR_ACCENT);
        title.setTextSize(12);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        title.setLetterSpacing(0.08f);
        heading.addView(title);

        if (subtitleText != null && !subtitleText.trim().isEmpty()) {
            TextView subtitle = smallText(subtitleText);
            subtitle.setTextSize(13);
            subtitle.setPadding(0, dp(3), 0, 0);
            heading.addView(subtitle);
        }
        return heading;
    }

    private LinearLayout section(String titleText, String bodyText, int accentColor) {
        LinearLayout box = cardContainer();

        TextView title = smallText(titleText);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        title.setTextColor(accentColor);
        title.setLetterSpacing(0.03f);
        TextView body = bodyText(bodyText);
        body.setPadding(0, dp(5), 0, 0);
        box.addView(title);
        box.addView(body);
        return box;
    }

    private LinearLayout statusCard(
            String titleText,
            String badgeText,
            String detailText,
            int accentColor
    ) {
        return statusMetricCard(
                titleText,
                badgeText,
                detailText,
                accentColor,
                null,
                null,
                null
        );
    }

    private LinearLayout statusMetricCard(
            String titleText,
            String badgeText,
            String detailText,
            int accentColor,
            String[] metricLabels,
            String[] metricValues,
            int[] metricColors
    ) {
        LinearLayout box = cardContainer();
        box.setBackground(roundedBackground(
                COLOR_SURFACE,
                withAlpha(accentColor, 0x88),
                16
        ));
        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.HORIZONTAL);
        header.setGravity(Gravity.CENTER_VERTICAL);

        TextView title = bodyText(titleText);
        title.setTextSize(16);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        header.addView(title, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
        header.addView(statusBadge(badgeText, accentColor));
        box.addView(header);

        if (detailText != null && !detailText.trim().isEmpty()) {
            TextView detail = smallText(detailText);
            detail.setTextSize(13);
            detail.setLineSpacing(0, 1.12f);
            detail.setPadding(0, dp(8), 0, 0);
            box.addView(detail);
        }
        if (metricLabels != null && metricValues != null && metricLabels.length > 0) {
            View metrics = metricGrid(metricLabels, metricValues, metricColors);
            LinearLayout.LayoutParams metricParams = new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT
            );
            metricParams.setMargins(0, dp(10), 0, 0);
            box.addView(metrics, metricParams);
        }
        return box;
    }

    private LinearLayout cardContainer() {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(dp(16), dp(15), dp(16), dp(15));
        box.setBackground(roundedBackground(COLOR_SURFACE, COLOR_BORDER, 16));
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        );
        params.setMargins(0, 0, 0, dp(12));
        box.setLayoutParams(params);
        return box;
    }

    private TextView statusBadge(String text, int color) {
        TextView badge = new TextView(this);
        badge.setText(text == null || text.trim().isEmpty() ? "확인 필요" : text);
        badge.setTextColor(COLOR_BACKGROUND);
        badge.setTextSize(11);
        badge.setTypeface(Typeface.DEFAULT_BOLD);
        badge.setGravity(Gravity.CENTER);
        badge.setPadding(dp(10), dp(5), dp(10), dp(5));
        badge.setBackground(roundedBackground(color, color, 20));
        badge.setContentDescription("상태 " + badge.getText());
        return badge;
    }

    private View metricGrid(String[] labels, String[] values, int[] colors) {
        LinearLayout grid = new LinearLayout(this);
        grid.setOrientation(LinearLayout.VERTICAL);
        int itemCount = Math.min(labels.length, values.length);
        final int columnCount = 2;
        for (int start = 0; start < itemCount; start += columnCount) {
            LinearLayout row = new LinearLayout(this);
            row.setOrientation(LinearLayout.HORIZONTAL);
            int end = Math.min(itemCount, start + columnCount);
            for (int index = start; index < end; index++) {
                int color = colors != null && index < colors.length ? colors[index] : COLOR_TEXT;
                row.addView(metricTile(labels[index], values[index], color));
            }
            if (end - start < columnCount) {
                for (int index = end; index < start + columnCount; index++) {
                    View spacer = new View(this);
                    row.addView(spacer, new LinearLayout.LayoutParams(0, 1, 1));
                }
            }
            LinearLayout.LayoutParams rowParams = new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT
            );
            if (start > 0) {
                rowParams.setMargins(0, dp(6), 0, 0);
            }
            grid.addView(row, rowParams);
        }
        return grid;
    }

    private View metricTile(String label, String value, int valueColor) {
        LinearLayout tile = new LinearLayout(this);
        tile.setOrientation(LinearLayout.VERTICAL);
        tile.setGravity(Gravity.CENTER_VERTICAL);
        tile.setPadding(dp(10), dp(9), dp(10), dp(9));
        tile.setMinimumHeight(dp(70));
        tile.setBackground(roundedBackground(COLOR_SURFACE_ALT, COLOR_BORDER, 10));
        tile.setContentDescription(label + ": " + value);
        tile.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_YES);

        TextView valueView = bodyText(value == null || value.trim().isEmpty() ? "-" : value);
        valueView.setTextSize(20);
        valueView.setTypeface(Typeface.DEFAULT_BOLD);
        valueView.setTextColor(valueColor);
        TextView labelView = smallText(label);
        labelView.setPadding(0, dp(2), 0, 0);
        valueView.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        labelView.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        tile.addView(valueView);
        tile.addView(labelView);

        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                0,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                1
        );
        params.setMargins(dp(3), 0, dp(3), 0);
        tile.setLayoutParams(params);
        return tile;
    }

    private GradientDrawable roundedBackground(int fillColor, int strokeColor, int radiusDp) {
        GradientDrawable background = new GradientDrawable();
        background.setColor(fillColor);
        background.setCornerRadius(dp(radiusDp));
        background.setStroke(dp(1), strokeColor);
        return background;
    }

    private int withAlpha(int color, int alpha) {
        return (color & 0x00ffffff) | ((alpha & 0xff) << 24);
    }

    private TextView bodyText(String text) {
        TextView view = new TextView(this);
        view.setText(text);
        view.setTextColor(COLOR_TEXT);
        view.setTextSize(14);
        view.setLineSpacing(0, 1.12f);
        return view;
    }

    private TextView smallText(String text) {
        TextView view = new TextView(this);
        view.setText(text);
        view.setTextColor(COLOR_MUTED);
        view.setTextSize(12);
        return view;
    }

    private void showSettingsDialog() {
        if (operationRunning) {
            showMessage("작업 실행 중", "원격 작업이 끝난 뒤 연결 설정을 변경하세요.");
            return;
        }
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(dp(20), dp(4), dp(20), 0);

        TextView urlLabel = smallText("API 주소");
        EditText urlInput = new EditText(this);
        urlInput.setSingleLine(true);
        urlInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        urlInput.setText(AppConfig.getBaseUrl(prefs));
        urlInput.setHint(AppConfig.DEFAULT_BASE_URL);

        TextView tokenLabel = smallText("API 토큰");
        EditText tokenInput = new EditText(this);
        tokenInput.setSingleLine(true);
        tokenInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        tokenInput.setText(AppConfig.getToken(prefs));
        tokenInput.setHint("MONITOR_APP_TOKEN");

        TextView testStatus = smallText("주소나 토큰을 변경하려면 연결 테스트가 필요합니다.");
        testStatus.setPadding(0, dp(8), 0, 0);
        final String[] verifiedFingerprint = {null};
        final ApiClient.RequestCancellation[] activeConnectionTest = {null};

        box.addView(urlLabel);
        box.addView(urlInput);
        box.addView(tokenLabel);
        box.addView(tokenInput);
        box.addView(testStatus);

        AlertDialog dialog = new AlertDialog.Builder(this)
                .setTitle("연결 설정")
                .setView(box)
                .setPositiveButton("저장", null)
                .setNeutralButton("연결 테스트", null)
                .setNegativeButton("취소", null)
                .create();
        dialog.setOnShowListener(ignored -> {
            Button saveButton = dialog.getButton(AlertDialog.BUTTON_POSITIVE);
            Button testButton = dialog.getButton(AlertDialog.BUTTON_NEUTRAL);
            saveButton.setOnClickListener(v -> {
                try {
                    String baseUrl = validateBaseUrl(urlInput.getText().toString());
                    String token = tokenInput.getText().toString().trim();
                    boolean connectionChanged = !baseUrl.equals(AppConfig.getBaseUrl(prefs))
                            || !token.equals(AppConfig.getToken(prefs));
                    if (connectionChanged
                            && !connectionFingerprint(baseUrl, token).equals(verifiedFingerprint[0])) {
                        testStatus.setTextColor(COLOR_WARNING);
                        testStatus.setText("변경한 주소와 토큰으로 연결 테스트를 먼저 완료하세요.");
                        return;
                    }
                    if (connectionChanged) {
                        MonitorJobService.invalidateForConnectionChange(this);
                        resetForegroundRefreshState();
                    }
                    prefs.edit()
                            .putString(AppConfig.KEY_BASE_URL, baseUrl)
                            .putString(AppConfig.KEY_APP_TOKEN, token)
                            .apply();
                    if (connectionChanged) {
                        MonitorJobService.resetProblemState(this);
                        MonitorStatusNotification.reset(this);
                    }
                    MonitorJobService.schedule(this);
                    dialog.dismiss();
                    refresh();
                    restartAutoRefreshTimer();
                    scheduleAutomaticUpdateCheck();
                } catch (Exception exception) {
                    testStatus.setTextColor(COLOR_CRITICAL);
                    testStatus.setText(safeMessage(exception));
                }
            });
            testButton.setOnClickListener(v -> testConnection(
                    dialog,
                    urlInput,
                    tokenInput,
                    testStatus,
                    saveButton,
                    testButton,
                    verifiedFingerprint,
                    activeConnectionTest
            ));
        });
        dialog.setOnDismissListener(ignored -> {
            ApiClient.RequestCancellation cancellation = activeConnectionTest[0];
            activeConnectionTest[0] = null;
            if (cancellation != null) {
                cancellation.cancel();
                releaseRequest(cancellation);
            }
        });
        dialog.show();
    }

    private void testConnection(
            AlertDialog dialog,
            EditText urlInput,
            EditText tokenInput,
            TextView statusView,
            Button saveButton,
            Button testButton,
            String[] verifiedFingerprint,
            ApiClient.RequestCancellation[] activeConnectionTest
    ) {
        final String baseUrl;
        try {
            baseUrl = validateBaseUrl(urlInput.getText().toString());
        } catch (Exception exception) {
            statusView.setTextColor(COLOR_CRITICAL);
            statusView.setText(safeMessage(exception));
            return;
        }
        String token = tokenInput.getText().toString().trim();
        verifiedFingerprint[0] = null;
        statusView.setTextColor(COLOR_WARNING);
        statusView.setText("연결 확인 중…");
        saveButton.setEnabled(false);
        testButton.setEnabled(false);

        ApiClient.RequestCancellation previousCancellation = activeConnectionTest[0];
        if (previousCancellation != null) {
            previousCancellation.cancel();
            releaseRequest(previousCancellation);
        }
        ApiClient.RequestCancellation cancellation = trackPauseRequest();
        activeConnectionTest[0] = cancellation;
        executor.execute(() -> {
            try {
                JSONObject data = ApiClient.getJson(
                        AppConfig.coreUrl(baseUrl),
                        token,
                        cancellation
                );
                CoreStatusSnapshot status = validateCoreContract(data);
                String result = "연결 성공 · 핵심 서비스 "
                        + status.healthyServiceCount() + "/" + status.services.size()
                        + " · Primary " + displayNode(status.observedPrimaryNode());
                mainHandler.post(() -> {
                    if (!dialog.isShowing()
                            || isDestroyed()
                            || isFinishing()) {
                        return;
                    }
                    statusView.setTextColor(COLOR_HEALTHY);
                    statusView.setText(result);
                    verifiedFingerprint[0] = connectionFingerprint(baseUrl, token);
                    saveButton.setEnabled(true);
                    testButton.setEnabled(true);
                });
            } catch (Exception exception) {
                mainHandler.post(() -> {
                    if (!dialog.isShowing()
                            || isDestroyed()
                            || isFinishing()) {
                        return;
                    }
                    statusView.setTextColor(COLOR_CRITICAL);
                    statusView.setText(getString(
                            R.string.connection_failed,
                            ApiErrorMessage.from(exception)
                    ));
                    verifiedFingerprint[0] = null;
                    saveButton.setEnabled(true);
                    testButton.setEnabled(true);
                });
            } finally {
                releaseRequest(cancellation);
                mainHandler.post(() -> {
                    if (activeConnectionTest[0] == cancellation) {
                        activeConnectionTest[0] = null;
                    }
                });
            }
        });
    }

    private String validateBaseUrl(String rawValue) {
        return EndpointPolicy.validateUsableHttpsBaseUrl(rawValue, AppConfig.DEFAULT_BASE_URL);
    }

    private String connectionFingerprint(String baseUrl, String token) {
        return baseUrl + "\n" + token;
    }

    private CoreStatusSnapshot validateCoreContract(JSONObject data) {
        return CoreStatusSnapshot.parse(data);
    }

    private void validateTailscaleContract(JSONObject data) {
        JSONObject summary = data.optJSONObject("summary");
        if (summary == null) {
            summary = data.optJSONObject("counts");
        }
        boolean available = data.optBoolean("available", false);
        if (!data.has("available")
                || data.optJSONArray("peers") == null
                || summary == null) {
            throw new IllegalStateException("Tailscale API 응답 형식이 올바르지 않습니다.");
        }
        if (available && data.optString("generated_at", "").trim().isEmpty()) {
            throw new IllegalStateException("Tailscale API 생성 시각이 없습니다.");
        }
        String[] countFields = {"online", "offline", "total"};
        for (String field : countFields) {
            if (!summary.has(field) || summary.isNull(field)) {
                throw new IllegalStateException(
                        "Tailscale API summary에 " + field + " 값이 없습니다."
                );
            }
        }
    }

    private void requestNotificationPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 1001);
        }
    }

    @Override
    public void onRequestPermissionsResult(
            int requestCode,
            String[] permissions,
            int[] grantResults
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == 1001) {
            boolean granted = grantResults.length > 0
                    && grantResults[0] == PackageManager.PERMISSION_GRANTED;
            boolean scheduled = MonitorJobService.schedule(this);
            if (granted && scheduled) {
                MonitorStatusNotification.showCachedOrChecking(this);
            }
        }
    }

    private void showMessage(String title, String message) {
        new AlertDialog.Builder(this)
                .setTitle(title)
                .setMessage(message)
                .setPositiveButton("확인", null)
                .show();
    }

    private void setEnabledRecursive(View view, boolean enabled) {
        view.setEnabled(enabled);
        if (view instanceof ViewGroup) {
            ViewGroup group = (ViewGroup) view;
            for (int i = 0; i < group.getChildCount(); i++) {
                setEnabledRecursive(group.getChildAt(i), enabled);
            }
        }
    }

    private int count(JSONObject counts, String name) {
        return counts == null ? 0 : counts.optInt(name, 0);
    }

    private int length(JSONArray rows) {
        return rows == null ? 0 : rows.length();
    }

    private int coreStateColor(CoreStatusSnapshot.State state) {
        if (state == CoreStatusSnapshot.State.HEALTHY) {
            return COLOR_HEALTHY;
        }
        if (state == CoreStatusSnapshot.State.CRITICAL) {
            return COLOR_CRITICAL;
        }
        return COLOR_WARNING;
    }

    private String coreStateLabel(CoreStatusSnapshot.State state) {
        return StatusNotificationPresentation.stateLabel(state);
    }

    private String displayNode(String node) {
        return node == null || node.trim().isEmpty() ? "확인 불가" : node.trim();
    }

    private String matchLabel(Boolean matches) {
        if (matches == null) {
            return "확인 불가";
        }
        return matches ? "일치" : "불일치";
    }

    private int statusColor(String status) {
        if ("healthy".equalsIgnoreCase(status)) {
            return COLOR_HEALTHY;
        }
        if ("critical".equalsIgnoreCase(status)) {
            return COLOR_CRITICAL;
        }
        return COLOR_WARNING;
    }

    private int crawlerLatestStatusColor(String status) {
        if ("success".equalsIgnoreCase(status)) {
            return COLOR_HEALTHY;
        }
        if ("failed".equalsIgnoreCase(status)) {
            return COLOR_CRITICAL;
        }
        if ("running".equalsIgnoreCase(status)) {
            return COLOR_INFO;
        }
        return COLOR_WARNING;
    }

    private String statusLabel(String status) {
        if ("healthy".equalsIgnoreCase(status)) {
            return "정상";
        }
        if ("critical".equalsIgnoreCase(status)) {
            return "장애";
        }
        if ("warning".equalsIgnoreCase(status)) {
            return "주의";
        }
        return "확인 필요";
    }

    private String operationKindLabel(String kind) {
        if ("restart".equalsIgnoreCase(kind)) {
            return "서비스 재시작";
        }
        if ("wol".equalsIgnoreCase(kind)) {
            return "Wake-on-LAN";
        }
        return kind.isEmpty() ? "원격 작업" : kind;
    }

    private String renderFailures(JSONArray rows) {
        if (rows == null || rows.length() == 0) {
            return "최근 실패 없음";
        }
        StringBuilder out = new StringBuilder("최근 실패:\n");
        int limit = Math.min(rows.length(), 5);
        for (int i = 0; i < limit; i++) {
            JSONObject row = rows.optJSONObject(i);
            if (row == null) {
                continue;
            }
            out.append("• ").append(row.optString("source_type", "-"))
                    .append(" / ").append(row.optString("target_key", "-"))
                    .append(" / ").append(row.optString("error_message", "-")).append('\n');
        }
        return out.toString().trim();
    }

    private void renderBackupCards(JSONObject backup) {
        JSONArray rows = backup == null ? null : backup.optJSONArray("items");
        boolean available = backup != null
                && (backup.optBoolean("available", false) || length(rows) > 0);
        if (!available || rows == null || rows.length() == 0) {
            content.addView(statusCard(
                    "백업",
                    "정보 없음",
                    "백업 실행 결과가 없습니다.",
                    COLOR_WARNING
            ));
            return;
        }

        int attention = 0;
        int critical = 0;
        int freshCount = 0;
        for (int i = 0; i < rows.length(); i++) {
            JSONObject row = rows.optJSONObject(i);
            if (row == null) {
                continue;
            }
            BackupPresentation backupPresentation = backupPresentation(row);
            if (backupPresentation.freshKnown && backupPresentation.fresh) {
                freshCount++;
            }
            if (backupPresentation.level == MonitorPresentation.LEVEL_CRITICAL) {
                critical++;
            } else if (backupPresentation.level != MonitorPresentation.LEVEL_HEALTHY) {
                attention++;
            }
        }

        int overallColor = critical > 0
                ? COLOR_CRITICAL
                : attention > 0 ? COLOR_WARNING : COLOR_HEALTHY;
        content.addView(statusMetricCard(
                "백업",
                critical > 0 ? "오류" : attention > 0 ? "확인 필요" : "정상",
                "서비스 active 여부가 아니라 최근 실행 시각과 신선도를 기준으로 판단합니다.",
                overallColor,
                new String[]{"전체", "최신", "확인 필요"},
                new String[]{
                        rows.length() + "개",
                        freshCount + "개",
                        (attention + critical) + "개"
                },
                new int[]{
                        COLOR_INFO,
                        freshCount == rows.length() ? COLOR_HEALTHY : COLOR_WARNING,
                        attention + critical == 0 ? COLOR_HEALTHY : overallColor
                }
        ));

        for (int i = 0; i < rows.length(); i++) {
            JSONObject row = rows.optJSONObject(i);
            if (row == null) {
                continue;
            }
            BackupPresentation presentation = backupPresentation(row);
            String node = row.optString("node", "-").trim();
            String name = firstNonEmpty(
                    row.optString("name", ""),
                    row.optString("unit", ""),
                    "백업 작업"
            );
            String lastEvent = formatOptionalTimestamp(presentation.lastEventAt);
            String freshLabel = presentation.freshKnown
                    ? presentation.fresh ? "최신" : "기한 초과"
                    : "정보 없음";
            StringBuilder detail = new StringBuilder();
            detail.append(presentation.lastEventLabel).append(' ').append(lastEvent)
                    .append('\n')
                    .append("신선도 ").append(freshLabel)
                    .append(" · 상태 ").append(backupHealthLabel(presentation.health));
            long ageSeconds = row.optLong("age_seconds", -1L);
            if (ageSeconds >= 0) {
                detail.append(" · 경과 ").append(formatAge(ageSeconds));
            }
            String freshAfter = row.optString("fresh_after_kst", "").trim();
            if (!freshAfter.isEmpty() && !"null".equalsIgnoreCase(freshAfter)) {
                detail.append("\n신선도 기준 ").append(formatTimestamp(freshAfter));
            }
            String error = firstNonEmpty(
                    row.optString("last_error", ""),
                    row.optString("error", "")
            );
            if (!error.isEmpty()) {
                detail.append("\n오류 ").append(error);
            }
            content.addView(statusCard(
                    ("-".equals(node) || node.isEmpty() ? "" : node + " · ") + name,
                    presentation.badge,
                    detail.toString(),
                    presentationColor(presentation.level)
            ));
        }
    }

    private BackupPresentation backupPresentation(JSONObject row) {
        boolean freshKnown = row.has("fresh_known")
                ? row.optBoolean("fresh_known", false)
                : row.has("fresh") && !row.isNull("fresh");
        boolean fresh = row.optBoolean("fresh", false);
        String lastSuccessAt = firstNonEmpty(
                row.optString("last_success_at", ""),
                row.optString("last_success", "")
        );
        String lastTriggeredAt = firstNonEmpty(
                row.optString("last_triggered_at", ""),
                row.optString("last_triggered_at_kst", "")
        );
        String lastEventAt = firstNonEmpty(lastSuccessAt, lastTriggeredAt);
        String lastEventLabel = lastSuccessAt.isEmpty() ? "최근 실행" : "최근 성공";
        boolean hasLastSuccess = !lastSuccessAt.isEmpty();
        // Backup items represent scheduled outcomes. active is a legacy freshness alias,
        // not a systemd runtime state, so an inactive one-shot must never be shown as stopped.
        boolean oneShot = true;
        String health = row.optString("health", "");
        int level = MonitorPresentation.backupLevel(
                oneShot,
                false,
                freshKnown,
                fresh,
                hasLastSuccess,
                health
        );
        String badge = MonitorPresentation.backupBadge(
                oneShot,
                false,
                freshKnown,
                fresh,
                hasLastSuccess,
                health
        );
        return new BackupPresentation(
                freshKnown,
                fresh,
                health,
                lastEventAt,
                lastEventLabel,
                level,
                badge
        );
    }

    private int presentationColor(int level) {
        if (level == MonitorPresentation.LEVEL_HEALTHY) {
            return COLOR_HEALTHY;
        }
        if (level == MonitorPresentation.LEVEL_CRITICAL) {
            return COLOR_CRITICAL;
        }
        if (level == MonitorPresentation.LEVEL_WARNING) {
            return COLOR_WARNING;
        }
        return COLOR_MUTED;
    }

    private String backupHealthLabel(String health) {
        String normalized = health == null ? "" : health.trim().toLowerCase(Locale.ROOT);
        if ("healthy".equals(normalized)
                || "ok".equals(normalized)
                || "success".equals(normalized)
                || "good".equals(normalized)) {
            return "정상";
        }
        if ("warning".equals(normalized)
                || "warn".equals(normalized)
                || "degraded".equals(normalized)
                || "stale".equals(normalized)) {
            return "주의";
        }
        if ("critical".equals(normalized)
                || "error".equals(normalized)
                || "failed".equals(normalized)
                || "failure".equals(normalized)
                || "unhealthy".equals(normalized)
                || "missing".equals(normalized)) {
            return "오류";
        }
        return normalized.isEmpty() ? "정보 없음" : health;
    }

    private String readableValue(Object value) {
        if (value == null || value == JSONObject.NULL) {
            return "";
        }
        if (value instanceof JSONArray) {
            JSONArray values = (JSONArray) value;
            StringBuilder result = new StringBuilder();
            for (int i = 0; i < values.length(); i++) {
                String item = values.optString(i, "").trim();
                if (item.isEmpty()) {
                    continue;
                }
                if (result.length() > 0) {
                    result.append(", ");
                }
                result.append(item);
            }
            return result.toString();
        }
        return String.valueOf(value).trim();
    }

    private String firstNonEmpty(String... values) {
        for (String value : values) {
            if (value != null
                    && !value.trim().isEmpty()
                    && !"null".equalsIgnoreCase(value.trim())) {
                return value.trim();
            }
        }
        return "";
    }

    private String formatAge(long seconds) {
        if (seconds < 60) {
            return seconds + "초";
        }
        long minutes = seconds / 60;
        if (minutes < 60) {
            return minutes + "분";
        }
        long hours = minutes / 60;
        if (hours < 48) {
            return hours + "시간";
        }
        return (hours / 24) + "일";
    }

    private String formatOptionalTimestamp(String value) {
        return value == null || value.trim().isEmpty() || "null".equalsIgnoreCase(value)
                ? "-"
                : formatTimestamp(value);
    }

    private String formatTimestamp(String value) {
        if (value == null || value.trim().isEmpty()) {
            return "-";
        }
        try {
            return OffsetDateTime.parse(value)
                    .atZoneSameInstant(ZoneId.systemDefault())
                    .format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss", Locale.KOREA));
        } catch (Exception ignored) {
            return value;
        }
    }

    private String currentTime() {
        return java.time.ZonedDateTime.now()
                .format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss", Locale.KOREA));
    }

    private String safeMessage(Exception exception) {
        String message = exception.getMessage();
        return message == null || message.trim().isEmpty()
                ? exception.getClass().getSimpleName()
                : message.trim();
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private static final class BackupPresentation {
        final boolean freshKnown;
        final boolean fresh;
        final String health;
        final String lastEventAt;
        final String lastEventLabel;
        final int level;
        final String badge;

        BackupPresentation(
                boolean freshKnown,
                boolean fresh,
                String health,
                String lastEventAt,
                String lastEventLabel,
                int level,
                String badge
        ) {
            this.freshKnown = freshKnown;
            this.fresh = fresh;
            this.health = health;
            this.lastEventAt = lastEventAt;
            this.lastEventLabel = lastEventLabel;
            this.level = level;
            this.badge = badge;
        }
    }
}
