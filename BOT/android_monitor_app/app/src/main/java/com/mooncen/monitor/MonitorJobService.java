package com.mooncen.monitor;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.job.JobInfo;
import android.app.job.JobParameters;
import android.app.job.JobScheduler;
import android.app.job.JobService;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.SystemClock;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.atomic.AtomicInteger;

public class MonitorJobService extends JobService {
    private static final int JOB_ID = 8_808;
    static final String CHANNEL_ALERTS = "mooncen_alerts";
    private static final int ALERT_ID = 20;

    private static final String KEY_PROBLEMS_INITIALIZED = "background_core_v1_problems_initialized";
    private static final String KEY_PROBLEM_KEYS = "background_core_v1_problem_keys";
    private static final String KEY_PROBLEM_TITLES = "background_core_v1_problem_titles";
    private static final Object CONNECTION_LOCK = new Object();
    private static final AtomicInteger CONNECTION_EPOCH = new AtomicInteger();

    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private volatile Future<?> runningTask;
    private volatile ApiClient.RequestCancellation runningCancellation;
    private volatile int runGeneration;

    public static boolean schedule(Context context) {
        Context appContext = context.getApplicationContext();
        JobScheduler scheduler =
                (JobScheduler) appContext.getSystemService(Context.JOB_SCHEDULER_SERVICE);
        SharedPreferences prefs = appContext.getSharedPreferences(
                AppConfig.PREFS_NAME,
                Context.MODE_PRIVATE
        );
        if (AppConfig.getToken(prefs).isEmpty()
                || !MonitorStatusNotification.canRunBackgroundMonitoring(appContext)) {
            if (scheduler != null) {
                scheduler.cancel(JOB_ID);
            }
            MonitorStatusNotification.cancel(appContext);
            return false;
        }
        if (scheduler == null) {
            return false;
        }
        JobInfo desiredJob = new JobInfo.Builder(
                JOB_ID,
                new ComponentName(appContext, MonitorJobService.class)
        )
                .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
                .setRequiresBatteryNotLow(true)
                .setPeriodic(
                        PowerPolicy.BACKGROUND_POLL_INTERVAL_MS,
                        PowerPolicy.BACKGROUND_POLL_FLEX_MS
                )
                .setPersisted(true)
                .build();
        if (sameSchedule(scheduler.getPendingJob(JOB_ID), desiredJob)) {
            return true;
        }
        return scheduler.schedule(desiredJob) == JobScheduler.RESULT_SUCCESS;
    }

    static void invalidateForConnectionChange(Context context) {
        synchronized (CONNECTION_LOCK) {
            CONNECTION_EPOCH.incrementAndGet();
        }
        JobScheduler scheduler = (JobScheduler) context.getApplicationContext()
                .getSystemService(Context.JOB_SCHEDULER_SERVICE);
        if (scheduler != null) {
            scheduler.cancel(JOB_ID);
        }
    }

    @SuppressWarnings("deprecation")
    private static boolean sameSchedule(JobInfo current, JobInfo desired) {
        return current != null
                && current.getId() == desired.getId()
                && current.getService().equals(desired.getService())
                && current.isPeriodic() == desired.isPeriodic()
                && current.getIntervalMillis() == desired.getIntervalMillis()
                && current.getFlexMillis() == desired.getFlexMillis()
                && current.getNetworkType() == desired.getNetworkType()
                && current.isPersisted() == desired.isPersisted()
                && current.isRequireBatteryNotLow() == desired.isRequireBatteryNotLow();
    }

    public static void resetProblemState(Context context) {
        context.getSharedPreferences(AppConfig.PREFS_NAME, Context.MODE_PRIVATE)
                .edit()
                .remove(KEY_PROBLEMS_INITIALIZED)
                .remove(KEY_PROBLEM_KEYS)
                .remove(KEY_PROBLEM_TITLES)
                .apply();
    }

    @Override
    public boolean onStartJob(JobParameters params) {
        SharedPreferences startPrefs = getSharedPreferences(
                AppConfig.PREFS_NAME,
                Context.MODE_PRIVATE
        );
        if (AppConfig.getToken(startPrefs).isEmpty()
                || !MonitorStatusNotification.canRunBackgroundMonitoring(this)) {
            JobScheduler scheduler =
                    (JobScheduler) getSystemService(Context.JOB_SCHEDULER_SERVICE);
            if (scheduler != null) {
                scheduler.cancel(JOB_ID);
            }
            MonitorStatusNotification.cancel(this);
            return false;
        }
        final int generation;
        final int connectionEpoch;
        final ApiClient.RequestCancellation cancellation = new ApiClient.RequestCancellation();
        synchronized (CONNECTION_LOCK) {
            generation = ++runGeneration;
            connectionEpoch = CONNECTION_EPOCH.get();
            runningCancellation = cancellation;
            runningTask = executor.submit(() -> {
            try {
                SharedPreferences prefs = getSharedPreferences(AppConfig.PREFS_NAME, Context.MODE_PRIVATE);
                if (AppConfig.migrateLegacyBaseUrl(prefs)) {
                    resetProblemState(this);
                }
                String requestUrl = AppConfig.coreUrl(prefs);
                String requestToken = AppConfig.getToken(prefs);
                String requestKey = RecentCoreSnapshotCache.requestKey(requestUrl, requestToken);
                if (applyRecentCoreCacheIfAvailable(
                        prefs,
                        requestKey,
                        -1L,
                        generation,
                        connectionEpoch,
                        requestUrl,
                        requestToken
                )) {
                    return;
                }
                if (!isCurrentRun(generation, connectionEpoch, requestUrl, requestToken)) {
                    return;
                }
                final long statusObservation = MonitorStatusNotification.beginObservation();
                try {
                    CoreRequestCoordinator.Result result = CoreRequestCoordinator.getJson(
                            requestUrl,
                            requestToken,
                            cancellation
                    );
                    JSONObject data = result.data();
                    if (!isCurrentRun(generation, connectionEpoch, requestUrl, requestToken)) {
                        return;
                    }
                    CoreStatusSnapshot status = CoreStatusSnapshot.parse(data);
                    if (!isCurrentRun(generation, connectionEpoch, requestUrl, requestToken)) {
                        return;
                    }
                    boolean accepted = applyCoreStatusIfCurrent(
                            prefs,
                            data,
                            status,
                            statusObservation,
                            requestKey,
                            true,
                            result.completedAtElapsed,
                            generation,
                            connectionEpoch,
                            requestUrl,
                            requestToken
                    );
                    if (!accepted) {
                        boolean appliedNewerResult = applyRecentCoreCacheIfAvailable(
                                prefs,
                                requestKey,
                                statusObservation,
                                generation,
                                connectionEpoch,
                                requestUrl,
                                requestToken
                        );
                        if (!appliedNewerResult) {
                            processCoreProblemsWithoutSummaryIfCurrent(
                                    prefs,
                                    status,
                                    generation,
                                    connectionEpoch,
                                    requestUrl,
                                    requestToken
                            );
                        }
                    }
                } catch (Exception exception) {
                    boolean currentRun;
                    boolean unavailableAccepted = false;
                    synchronized (CONNECTION_LOCK) {
                        currentRun = isCurrentRun(
                                generation,
                                connectionEpoch,
                                requestUrl,
                                requestToken
                        );
                        if (currentRun) {
                            unavailableAccepted = MonitorStatusNotification.showUnavailable(
                                    this,
                                    ApiErrorMessage.from(exception),
                                    statusObservation
                            );
                        }
                    }
                    if (currentRun && !unavailableAccepted) {
                        applyRecentCoreCacheIfAvailable(
                                prefs,
                                requestKey,
                                statusObservation,
                                generation,
                                connectionEpoch,
                                requestUrl,
                                requestToken
                        );
                    }
                    // An unavailable monitor is not evidence that a service failed or recovered.
                }
            } finally {
                synchronized (CONNECTION_LOCK) {
                    if (generation == runGeneration) {
                        runningTask = null;
                        runningCancellation = null;
                        jobFinished(params, false);
                    }
                }
            }
            });
        }
        return true;
    }

    private boolean isCurrentRun(int generation) {
        return generation == runGeneration && !Thread.currentThread().isInterrupted();
    }

    private boolean isCurrentRun(
            int generation,
            int connectionEpoch,
            String requestUrl,
            String requestToken
    ) {
        if (!isCurrentRun(generation) || connectionEpoch != CONNECTION_EPOCH.get()) {
            return false;
        }
        SharedPreferences prefs = getSharedPreferences(AppConfig.PREFS_NAME, Context.MODE_PRIVATE);
        return requestUrl.equals(AppConfig.coreUrl(prefs))
                && requestToken.equals(AppConfig.getToken(prefs));
    }

    private boolean applyCoreStatusIfCurrent(
            SharedPreferences prefs,
            JSONObject data,
            CoreStatusSnapshot status,
            long observationSequence,
            String requestKey,
            boolean recordResult,
            long recordedAtElapsed,
            int generation,
            int connectionEpoch,
            String requestUrl,
            String requestToken
    ) {
        if (!isCurrentRun(generation, connectionEpoch, requestUrl, requestToken)) {
            return false;
        }
        CoreObservation observation = observeCoreProblems(status);
        synchronized (CONNECTION_LOCK) {
            if (!isCurrentRun(generation, connectionEpoch, requestUrl, requestToken)) {
                return false;
            }
            return MonitorStatusNotification.showSummaryAndRunIfCurrent(
                    this,
                    status,
                    observationSequence,
                    () -> {
                        processProblemChanges(
                                prefs,
                                observation.criticalProblems,
                                observation.healthyKeys
                        );
                        if (recordResult && data != null) {
                            RecentCoreSnapshotCache.record(
                                    requestKey,
                                    data,
                                    status,
                                    observationSequence,
                                    recordedAtElapsed
                            );
                        }
                    }
            );
        }
    }

    private boolean applyRecentCoreCacheIfAvailable(
            SharedPreferences prefs,
            String requestKey,
            long newerThanObservation,
            int generation,
            int connectionEpoch,
            String requestUrl,
            String requestToken
    ) {
        RecentCoreSnapshotCache.Entry recent = RecentCoreSnapshotCache.getFresh(
                requestKey,
                SystemClock.elapsedRealtime()
        );
        return recent != null
                && recent.observationSequence > newerThanObservation
                && applyCoreStatusIfCurrent(
                prefs,
                null,
                recent.snapshot,
                recent.observationSequence,
                requestKey,
                false,
                0L,
                generation,
                connectionEpoch,
                requestUrl,
                requestToken
        );
    }

    /**
     * A newer foreground "unavailable" observation can supersede this job's summary even though
     * this response is still valid service evidence. Preserve alert/recovery state without
     * replacing the newer foreground notification.
     */
    private boolean processCoreProblemsWithoutSummaryIfCurrent(
            SharedPreferences prefs,
            CoreStatusSnapshot status,
            int generation,
            int connectionEpoch,
            String requestUrl,
            String requestToken
    ) {
        if (!isCurrentRun(generation, connectionEpoch, requestUrl, requestToken)) {
            return false;
        }
        CoreObservation observation = observeCoreProblems(status);
        synchronized (CONNECTION_LOCK) {
            if (!isCurrentRun(generation, connectionEpoch, requestUrl, requestToken)) {
                return false;
            }
            processProblemChanges(
                    prefs,
                    observation.criticalProblems,
                    observation.healthyKeys
            );
            return true;
        }
    }

    @Override
    public boolean onStopJob(JobParameters params) {
        ApiClient.RequestCancellation cancellation;
        Future<?> task;
        synchronized (CONNECTION_LOCK) {
            runGeneration++;
            cancellation = runningCancellation;
            runningCancellation = null;
            task = runningTask;
            runningTask = null;
        }
        if (cancellation != null) {
            cancellation.cancel();
        }
        if (task != null) {
            task.cancel(true);
        }
        return false;
    }

    @Override
    public void onDestroy() {
        ApiClient.RequestCancellation cancellation;
        Future<?> task;
        synchronized (CONNECTION_LOCK) {
            runGeneration++;
            cancellation = runningCancellation;
            runningCancellation = null;
            task = runningTask;
            runningTask = null;
        }
        if (cancellation != null) {
            cancellation.cancel();
        }
        if (task != null) {
            task.cancel(true);
        }
        executor.shutdownNow();
        super.onDestroy();
    }

    @SuppressLint("ApplySharedPref")
    private void processProblemChanges(
            SharedPreferences prefs,
            List<Problem> criticalProblems,
            Set<String> healthyKeys
    ) {
        boolean initialized = prefs.getBoolean(KEY_PROBLEMS_INITIALIZED, false);
        Set<String> previousKeys = new HashSet<>(
                prefs.getStringSet(KEY_PROBLEM_KEYS, new HashSet<>())
        );
        Map<String, String> previousTitles = readTitles(
                prefs.getString(KEY_PROBLEM_TITLES, "{}")
        );

        Set<String> currentKeys = new LinkedHashSet<>();
        Map<String, Problem> currentProblems = new LinkedHashMap<>();
        JSONObject currentTitles = new JSONObject();
        for (Problem problem : criticalProblems) {
            currentKeys.add(problem.key);
            currentProblems.put(problem.key, problem);
            try {
                currentTitles.put(problem.key, problem.title);
            } catch (JSONException ignored) {
                // JSONObject only rejects unsupported values; both entries are strings.
            }
        }
        currentKeys = ProblemState.reconcileCore(previousKeys, currentKeys, healthyKeys);
        for (String currentKey : currentKeys) {
            if (currentProblems.containsKey(currentKey)) {
                continue;
            }
            String previousTitle = previousTitles.getOrDefault(currentKey, currentKey);
            Problem retainedProblem = new Problem(currentKey, previousTitle, "");
            currentProblems.put(currentKey, retainedProblem);
            try {
                currentTitles.put(currentKey, previousTitle);
            } catch (JSONException ignored) {
                // JSONObject only rejects unsupported values; both entries are strings.
            }
        }

        Set<String> added = new LinkedHashSet<>(currentKeys);
        added.removeAll(previousKeys);
        Set<String> recovered = new LinkedHashSet<>(previousKeys);
        recovered.removeAll(currentKeys);

        Map<String, String> currentTitleMap = readTitles(currentTitles.toString());
        boolean preferenceChanged = !initialized
                || !previousKeys.equals(currentKeys)
                || !previousTitles.equals(currentTitleMap);
        if (preferenceChanged) {
            prefs.edit()
                    .putBoolean(KEY_PROBLEMS_INITIALIZED, true)
                    .putStringSet(KEY_PROBLEM_KEYS, new HashSet<>(currentKeys))
                    .putString(KEY_PROBLEM_TITLES, currentTitles.toString())
                    .commit();
        }

        if (!initialized && currentKeys.isEmpty()) {
            return;
        }
        if (added.isEmpty() && recovered.isEmpty()) {
            return;
        }
        notifyChanges(added, recovered, currentProblems, previousTitles);
    }

    private Map<String, String> readTitles(String jsonText) {
        Map<String, String> result = new LinkedHashMap<>();
        try {
            JSONObject json = new JSONObject(jsonText == null ? "{}" : jsonText);
            JSONArray names = json.names();
            if (names == null) {
                return result;
            }
            for (int i = 0; i < names.length(); i++) {
                String key = names.optString(i);
                result.put(key, json.optString(key, key));
            }
        } catch (JSONException ignored) {
            // A damaged cache is safely replaced at the end of the next poll.
        }
        return result;
    }

    private CoreObservation observeCoreProblems(CoreStatusSnapshot status) {
        List<Problem> criticalProblems = new ArrayList<>();
        Set<String> healthyKeys = new LinkedHashSet<>();
        for (CoreStatusSnapshot.Service service : status.services) {
            String key = "core:" + service.key;
            if (service.state == CoreStatusSnapshot.State.CRITICAL) {
                String detail = service.detail;
                if (detail.isEmpty()) {
                    detail = "실행 "
                            + StatusNotificationPresentation.booleanLabel(service.runtimeOk)
                            + " · 실제 기능 "
                            + StatusNotificationPresentation.booleanLabel(service.functionalOk);
                }
                criticalProblems.add(new Problem(
                        key,
                        CoreStatusSnapshot.serviceLabel(service.key) + " 핵심 서비스 장애",
                        detail
                ));
            } else if (service.state == CoreStatusSnapshot.State.HEALTHY) {
                healthyKeys.add(key);
            }
        }

        String primaryKey = "core:primary";
        if (status.primary.state == CoreStatusSnapshot.State.CRITICAL) {
            String observed = status.observedPrimaryNode().isEmpty()
                    ? "확인 불가"
                    : status.observedPrimaryNode();
            String expected = status.expectedPrimaryNode().isEmpty()
                    ? "확인 불가"
                    : status.expectedPrimaryNode();
            criticalProblems.add(new Problem(
                    primaryKey,
                    "Primary 서버 이상",
                    "관측 " + observed + " · 예상 " + expected
            ));
        } else if (status.primary.state == CoreStatusSnapshot.State.HEALTHY) {
            healthyKeys.add(primaryKey);
        }
        return new CoreObservation(criticalProblems, healthyKeys);
    }

    private void notifyChanges(
            Set<String> added,
            Set<String> recovered,
            Map<String, Problem> currentProblems,
            Map<String, String> previousTitles
    ) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) {
            return;
        }

        createAlertChannel();
        String title;
        if (!added.isEmpty() && !recovered.isEmpty()) {
            title = "상태 변경 · 장애 " + added.size() + " · 복구 " + recovered.size();
        } else if (!added.isEmpty()) {
            title = "새 장애 " + added.size() + "건";
        } else {
            title = "복구 " + recovered.size() + "건";
        }

        StringBuilder body = new StringBuilder();
        int lineCount = 0;
        for (String key : added) {
            Problem problem = currentProblems.get(key);
            if (problem == null) {
                continue;
            }
            appendLine(body, "장애 · " + problem.title);
            if (!problem.detail.isEmpty() && lineCount < 5) {
                appendLine(body, "  " + problem.detail);
                lineCount++;
            }
            lineCount++;
            if (lineCount >= 6) {
                break;
            }
        }
        if (lineCount < 6) {
            for (String key : recovered) {
                appendLine(body, "복구 · " + previousTitles.getOrDefault(key, key));
                lineCount++;
                if (lineCount >= 6) {
                    break;
                }
            }
        }

        Notification notification = new Notification.Builder(this, CHANNEL_ALERTS)
                .setSmallIcon(R.drawable.ic_stat_monitor)
                .setContentTitle(title)
                .setContentText(firstLine(body.toString()))
                .setStyle(new Notification.BigTextStyle().bigText(body.toString()))
                .setContentIntent(mainPendingIntent())
                .setCategory(Notification.CATEGORY_STATUS)
                .setAutoCancel(true)
                .build();
        NotificationManager manager =
                (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        manager.notify(ALERT_ID, notification);
    }

    private void createAlertChannel() {
        NotificationManager manager =
                (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        NotificationChannel alerts = new NotificationChannel(
                CHANNEL_ALERTS,
                "상태 변경 알림",
                NotificationManager.IMPORTANCE_HIGH
        );
        alerts.setDescription("확인된 핵심 서비스 장애와 복구 상태를 알려줍니다.");
        manager.createNotificationChannel(alerts);
    }

    private PendingIntent mainPendingIntent() {
        Intent intent = new Intent(this, MainActivity.class);
        intent.setFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        return PendingIntent.getActivity(
                this,
                0,
                intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );
    }

    private static void appendLine(StringBuilder body, String value) {
        if (body.length() > 0) {
            body.append('\n');
        }
        body.append(value);
    }

    private static String firstLine(String value) {
        int newline = value.indexOf('\n');
        return newline >= 0 ? value.substring(0, newline) : value;
    }

    private static final class Problem {
        final String key;
        final String title;
        final String detail;

        Problem(String key, String title, String detail) {
            this.key = key;
            this.title = title;
            this.detail = detail;
        }
    }

    private static final class CoreObservation {
        final List<Problem> criticalProblems;
        final Set<String> healthyKeys;

        CoreObservation(List<Problem> criticalProblems, Set<String> healthyKeys) {
            this.criticalProblems = criticalProblems;
            this.healthyKeys = healthyKeys;
        }
    }
}
