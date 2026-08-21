package com.mooncen.monitor;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.os.Build;
import android.service.notification.StatusBarNotification;
import android.text.format.DateFormat;

import org.json.JSONObject;

import java.util.Date;
import java.util.Objects;
import java.util.concurrent.atomic.AtomicLong;

final class MonitorStatusNotification {
    private static final String CHANNEL_STATUS = "mooncen_current_status";
    private static final int STATUS_ID = 10;

    private static final String KEY_INITIALIZED = "core_status_notification_v1_initialized";
    private static final String KEY_TITLE = "core_status_notification_v1_title";
    private static final String KEY_TEXT = "core_status_notification_v1_text";
    private static final String KEY_BIG_TEXT = "core_status_notification_v1_big_text";
    private static final String KEY_LEVEL = "core_status_notification_v1_level";
    private static final String KEY_CHECKED_AT = "core_status_notification_v1_checked_at";
    private static final String KEY_PUBLISHED_AT = "core_status_notification_v1_published_at";
    private static final Object PUBLISH_LOCK = new Object();
    private static final AtomicLong NEXT_OBSERVATION_SEQUENCE = new AtomicLong();
    private static long latestObservationSequence;

    private MonitorStatusNotification() {
    }

    static boolean showSummary(Context context, JSONObject data) {
        return showSummary(context, CoreStatusSnapshot.parse(data), beginObservation());
    }

    static boolean showSummary(Context context, JSONObject data, long observationSequence) {
        return showSummary(context, CoreStatusSnapshot.parse(data), observationSequence);
    }

    static boolean showSummary(Context context, CoreStatusSnapshot status) {
        return showSummary(context, status, beginObservation());
    }

    static boolean showSummary(
            Context context,
            CoreStatusSnapshot status,
            long observationSequence
    ) {
        StatusNotificationPresentation.Snapshot snapshot =
                StatusNotificationPresentation.summary(status);
        return publish(
                context,
                snapshot,
                System.currentTimeMillis(),
                true,
                observationSequence,
                false
        );
    }

    static boolean showSummaryAndRunIfCurrent(
            Context context,
            CoreStatusSnapshot status,
            long observationSequence,
            Runnable onAccepted
    ) {
        StatusNotificationPresentation.Snapshot snapshot =
                StatusNotificationPresentation.summary(status);
        synchronized (PUBLISH_LOCK) {
            if (!acceptObservation(observationSequence)) {
                return false;
            }
            publishLocked(context, snapshot, System.currentTimeMillis(), true);
            onAccepted.run();
            return true;
        }
    }

    static boolean showUnavailable(Context context, String detail) {
        return showUnavailable(context, detail, beginObservation());
    }

    static boolean showUnavailable(Context context, String detail, long observationSequence) {
        return publish(
                context,
                StatusNotificationPresentation.unavailable(detail),
                System.currentTimeMillis(),
                true,
                observationSequence,
                true
        );
    }

    static long beginObservation() {
        return NEXT_OBSERVATION_SEQUENCE.incrementAndGet();
    }

    static void showCachedOrChecking(Context context) {
        synchronized (PUBLISH_LOCK) {
            SharedPreferences prefs = prefs(context);
            if (!prefs.getBoolean(KEY_INITIALIZED, false)) {
                publish(
                        context,
                        StatusNotificationPresentation.checking(),
                        System.currentTimeMillis(),
                        false,
                        0L,
                        false
                );
                return;
            }
            StatusNotificationPresentation.Snapshot snapshot =
                    new StatusNotificationPresentation.Snapshot(
                            prefs.getString(KEY_TITLE, "문센 핵심 서비스"),
                            prefs.getString(KEY_TEXT, "핵심 서비스 상태를 확인하세요."),
                            prefs.getString(KEY_BIG_TEXT, "앱을 열어 Primary와 핵심 상태를 갱신하세요."),
                            prefs.getInt(KEY_LEVEL, StatusNotificationPresentation.LEVEL_WARNING)
                    );
            publish(
                    context,
                    snapshot,
                    prefs.getLong(KEY_CHECKED_AT, System.currentTimeMillis()),
                    false,
                    0L,
                    false
            );
        }
    }

    static void reset(Context context) {
        synchronized (PUBLISH_LOCK) {
            prefs(context).edit()
                    .remove(KEY_INITIALIZED)
                    .remove(KEY_TITLE)
                    .remove(KEY_TEXT)
                    .remove(KEY_BIG_TEXT)
                    .remove(KEY_LEVEL)
                    .remove(KEY_CHECKED_AT)
                    .remove(KEY_PUBLISHED_AT)
                    .apply();
            showCachedOrChecking(context);
        }
    }

    static void cancel(Context context) {
        Context appContext = applicationContext(context);
        NotificationManager manager =
                (NotificationManager) appContext.getSystemService(Context.NOTIFICATION_SERVICE);
        if (manager != null) {
            manager.cancel(STATUS_ID);
        }
    }

    static void ensureVisible(Context context) {
        Context appContext = applicationContext(context);
        if (!canNotify(appContext)) {
            return;
        }
        NotificationManager manager =
                (NotificationManager) appContext.getSystemService(Context.NOTIFICATION_SERVICE);
        if (manager == null) {
            return;
        }
        synchronized (PUBLISH_LOCK) {
            for (StatusBarNotification active : manager.getActiveNotifications()) {
                if (active.getId() == STATUS_ID) {
                    return;
                }
            }
            showCachedOrChecking(appContext);
        }
    }

    private static boolean publish(
            Context context,
            StatusNotificationPresentation.Snapshot snapshot,
            long checkedAt,
            boolean cache,
            long observationSequence,
            boolean unavailableObservation
    ) {
        synchronized (PUBLISH_LOCK) {
            if (cache && observationSequence > 0L) {
                if (!acceptObservation(observationSequence)) {
                    return false;
                }
                if (unavailableObservation) {
                    RecentCoreSnapshotCache.invalidateThrough(observationSequence);
                }
            }
            publishLocked(context, snapshot, checkedAt, cache);
            return true;
        }
    }

    private static boolean acceptObservation(long observationSequence) {
        if (observationSequence < latestObservationSequence) {
            return false;
        }
        latestObservationSequence = observationSequence;
        return true;
    }

    private static void publishLocked(
            Context context,
            StatusNotificationPresentation.Snapshot snapshot,
            long checkedAt,
            boolean cache
    ) {
        Context appContext = applicationContext(context);
        boolean notificationAllowed = canNotify(appContext);
        SharedPreferences preferences = cache ? prefs(appContext) : null;
        boolean initialized = false;
        boolean sameContent = false;
        if (cache) {
            initialized = preferences.getBoolean(KEY_INITIALIZED, false);
            sameContent = Objects.equals(
                    preferences.getString(KEY_TITLE, null),
                    snapshot.title
            ) && Objects.equals(
                    preferences.getString(KEY_TEXT, null),
                    snapshot.text
            ) && Objects.equals(
                    preferences.getString(KEY_BIG_TEXT, null),
                    snapshot.bigText
            ) && preferences.getInt(KEY_LEVEL, Integer.MIN_VALUE) == snapshot.level;
            long lastPublishedAt = preferences.getLong(KEY_PUBLISHED_AT, 0L);
            boolean publishDue = PowerPolicy.shouldPublishStatus(
                    initialized,
                    sameContent,
                    lastPublishedAt,
                    checkedAt
            );
            if (!notificationAllowed) {
                if (!initialized || !sameContent) {
                    saveSnapshot(preferences, snapshot, checkedAt, false, 0L);
                }
                return;
            }
            if (!publishDue) {
                return;
            }
        } else if (!notificationAllowed) {
            return;
        }

        NotificationManager manager =
                (NotificationManager) appContext.getSystemService(Context.NOTIFICATION_SERVICE);
        if (manager == null) {
            return;
        }
        createStatusChannel(manager);
        String checkedText = "마지막 확인 "
                + DateFormat.getTimeFormat(appContext).format(new Date(checkedAt));
        Notification notification = new Notification.Builder(appContext, CHANNEL_STATUS)
                .setSmallIcon(R.drawable.ic_stat_monitor)
                .setContentTitle(snapshot.title)
                .setContentText(snapshot.text)
                .setStyle(new Notification.BigTextStyle().bigText(snapshot.bigText))
                .setSubText(checkedText)
                .setWhen(checkedAt)
                .setShowWhen(true)
                .setColor(colorForLevel(snapshot.level))
                .setContentIntent(mainPendingIntent(appContext))
                .setCategory(Notification.CATEGORY_STATUS)
                .setOnlyAlertOnce(true)
                .setOngoing(true)
                .setAutoCancel(false)
                .build();
        manager.notify(STATUS_ID, notification);
        if (cache) {
            saveSnapshot(
                    preferences,
                    snapshot,
                    checkedAt,
                    true,
                    System.currentTimeMillis()
            );
        }
    }

    private static void saveSnapshot(
            SharedPreferences preferences,
            StatusNotificationPresentation.Snapshot snapshot,
            long checkedAt,
            boolean published,
            long publishedAt
    ) {
        SharedPreferences.Editor editor = preferences.edit()
                .putBoolean(KEY_INITIALIZED, true)
                .putString(KEY_TITLE, snapshot.title)
                .putString(KEY_TEXT, snapshot.text)
                .putString(KEY_BIG_TEXT, snapshot.bigText)
                .putInt(KEY_LEVEL, snapshot.level)
                .putLong(KEY_CHECKED_AT, checkedAt);
        if (published) {
            editor.putLong(KEY_PUBLISHED_AT, publishedAt);
        }
        editor.apply();
    }

    static boolean canNotify(Context context) {
        Context appContext = applicationContext(context);
        NotificationManager manager = allowedNotificationManager(appContext);
        return manager != null && isChannelEnabled(manager, CHANNEL_STATUS);
    }

    static boolean canRunBackgroundMonitoring(Context context) {
        Context appContext = applicationContext(context);
        NotificationManager manager = allowedNotificationManager(appContext);
        return manager != null
                && (isChannelEnabled(manager, CHANNEL_STATUS)
                || isChannelEnabled(manager, MonitorJobService.CHANNEL_ALERTS));
    }

    private static NotificationManager allowedNotificationManager(Context appContext) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
                && appContext.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) {
            return null;
        }
        NotificationManager manager =
                (NotificationManager) appContext.getSystemService(Context.NOTIFICATION_SERVICE);
        return manager != null && manager.areNotificationsEnabled() ? manager : null;
    }

    private static boolean isChannelEnabled(NotificationManager manager, String channelId) {
        NotificationChannel channel = manager.getNotificationChannel(channelId);
        return channel == null || channel.getImportance() != NotificationManager.IMPORTANCE_NONE;
    }

    private static Context applicationContext(Context context) {
        Context appContext = context.getApplicationContext();
        return appContext != null ? appContext : context;
    }

    private static void createStatusChannel(NotificationManager manager) {
        NotificationChannel channel = new NotificationChannel(
                CHANNEL_STATUS,
                "핵심 서비스 현재 상태",
                NotificationManager.IMPORTANCE_LOW
        );
        channel.setDescription("Primary와 DB, FRONT, BACKEND, CRAWLER의 마지막 상태를 표시합니다.");
        channel.setShowBadge(false);
        channel.enableVibration(false);
        channel.setSound(null, null);
        manager.createNotificationChannel(channel);
    }

    private static PendingIntent mainPendingIntent(Context context) {
        Intent intent = new Intent(context, MainActivity.class);
        intent.setFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        return PendingIntent.getActivity(
                context,
                1,
                intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );
    }

    private static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences(AppConfig.PREFS_NAME, Context.MODE_PRIVATE);
    }

    private static int colorForLevel(int level) {
        if (level == StatusNotificationPresentation.LEVEL_HEALTHY) {
            return Color.rgb(88, 184, 143);
        }
        if (level == StatusNotificationPresentation.LEVEL_CRITICAL) {
            return Color.rgb(255, 107, 107);
        }
        return Color.rgb(255, 200, 87);
    }
}
