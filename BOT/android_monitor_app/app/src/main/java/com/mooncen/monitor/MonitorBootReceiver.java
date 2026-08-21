package com.mooncen.monitor;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

public class MonitorBootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        String action = intent == null ? "" : intent.getAction();
        if (!Intent.ACTION_BOOT_COMPLETED.equals(action)
                && !Intent.ACTION_MY_PACKAGE_REPLACED.equals(action)) {
            return;
        }
        if (MonitorJobService.schedule(context)) {
            MonitorStatusNotification.showCachedOrChecking(context);
        } else {
            MonitorStatusNotification.cancel(context);
        }
    }
}
