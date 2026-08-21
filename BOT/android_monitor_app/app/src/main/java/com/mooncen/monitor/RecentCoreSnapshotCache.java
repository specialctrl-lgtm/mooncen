package com.mooncen.monitor;

import org.json.JSONException;
import org.json.JSONObject;

/** Process-local handoff for avoiding a duplicate core request after a recent accepted result. */
final class RecentCoreSnapshotCache {
    private static Entry latest;
    private static long latestAcceptedObservationSequence;
    private static boolean latestAcceptedObservationUnavailable;

    private RecentCoreSnapshotCache() {
    }

    static String requestKey(String url, String token) {
        return (url == null ? "" : url) + '\n' + (token == null ? "" : token.trim());
    }

    static synchronized void record(
            String requestKey,
            JSONObject data,
            CoreStatusSnapshot snapshot,
            long observationSequence,
            long recordedAtElapsed
    ) {
        if (requestKey == null
                || data == null
                || snapshot == null
                || recordedAtElapsed <= 0L) {
            return;
        }
        if (observationSequence < latestAcceptedObservationSequence) {
            return;
        }
        if (latest != null
                && latest.requestKey.equals(requestKey)
                && observationSequence < latest.observationSequence) {
            return;
        }
        latest = new Entry(
                requestKey,
                data.toString(),
                snapshot,
                observationSequence,
                recordedAtElapsed
        );
        latestAcceptedObservationSequence = Math.max(
                latestAcceptedObservationSequence,
                observationSequence
        );
        latestAcceptedObservationUnavailable = false;
    }

    static synchronized Entry getFresh(String requestKey, long nowElapsed) {
        if (latest == null
                || !latest.requestKey.equals(requestKey)
                || !PowerPolicy.isBackgroundCoreReuseFresh(
                latest.recordedAtElapsed,
                nowElapsed
        )) {
            return null;
        }
        return latest;
    }

    static synchronized void clear() {
        latest = null;
        latestAcceptedObservationSequence = 0L;
        latestAcceptedObservationUnavailable = false;
    }

    static synchronized void invalidateThrough(long observationSequence) {
        if (observationSequence >= latestAcceptedObservationSequence) {
            latestAcceptedObservationSequence = observationSequence;
            latestAcceptedObservationUnavailable = true;
        }
        if (latest != null && latest.observationSequence <= observationSequence) {
            latest = null;
        }
    }

    static synchronized long latestAcceptedObservationSequence() {
        return latestAcceptedObservationSequence;
    }

    static synchronized boolean latestAcceptedObservationUnavailable() {
        return latestAcceptedObservationUnavailable;
    }

    static final class Entry {
        final String requestKey;
        final CoreStatusSnapshot snapshot;
        final long observationSequence;
        final long recordedAtElapsed;
        private final String rawJson;

        private Entry(
                String requestKey,
                String rawJson,
                CoreStatusSnapshot snapshot,
                long observationSequence,
                long recordedAtElapsed
        ) {
            this.requestKey = requestKey;
            this.rawJson = rawJson;
            this.snapshot = snapshot;
            this.observationSequence = observationSequence;
            this.recordedAtElapsed = recordedAtElapsed;
        }

        JSONObject data() {
            try {
                return new JSONObject(rawJson);
            } catch (JSONException ignored) {
                return null;
            }
        }
    }
}
