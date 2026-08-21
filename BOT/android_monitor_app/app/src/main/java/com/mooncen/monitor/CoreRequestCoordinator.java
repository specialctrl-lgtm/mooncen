package com.mooncen.monitor;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.IOException;
import java.io.InterruptedIOException;
import java.net.SocketTimeoutException;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.concurrent.FutureTask;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.SynchronousQueue;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;

/** Shares only identical, read-only core GETs across foreground and background callers. */
final class CoreRequestCoordinator {
    private static final long WAIT_TIMEOUT_MS = 22_000L;
    private static final long TERMINAL_GRACE_MS = 2_000L;
    private static final int MAX_TRACKED_REQUESTS = 4;
    private static final Object LOCK = new Object();
    private static final Map<String, Flight> FLIGHTS = new HashMap<>();
    private static final ThreadPoolExecutor TRANSPORT_EXECUTOR = createTransportExecutor();
    private static final ElapsedClock SYSTEM_CLOCK = android.os.SystemClock::elapsedRealtime;

    private CoreRequestCoordinator() {
    }

    static Result getJson(
            String url,
            String token,
            ApiClient.RequestCancellation callerCancellation
    ) throws IOException, JSONException {
        return getJson(url, token, callerCancellation, true);
    }

    static Result getJson(
            String url,
            String token,
            ApiClient.RequestCancellation callerCancellation,
            boolean allowCompletedSuccess
    ) throws IOException, JSONException {
        return getJson(
                url,
                token,
                callerCancellation,
                allowCompletedSuccess,
                SYSTEM_CLOCK,
                (requestUrl, requestToken, transportCancellation) -> ApiClient.getJson(
                        requestUrl,
                        requestToken,
                        transportCancellation
                )
        );
    }

    static Result getJson(
            String url,
            String token,
            ApiClient.RequestCancellation callerCancellation,
            ElapsedClock clock,
            Transport transport
    ) throws IOException, JSONException {
        return getJson(url, token, callerCancellation, true, clock, transport);
    }

    static Result getJson(
            String url,
            String token,
            ApiClient.RequestCancellation callerCancellation,
            boolean allowCompletedSuccess,
            ElapsedClock clock,
            Transport transport
    ) throws IOException, JSONException {
        throwIfCallerCancelled(callerCancellation);
        String normalizedToken = token == null ? "" : token.trim();
        String requestKey = requestKey(url, normalizedToken);
        Flight flight;
        boolean created = false;
        synchronized (LOCK) {
            long nowElapsed = clock.now();
            pruneExpiredLocked(nowElapsed);
            flight = FLIGHTS.get(requestKey);
            if (flight != null
                    && (flight.state == State.FAILED
                    || (flight.state == State.SUCCEEDED && !allowCompletedSuccess))) {
                FLIGHTS.remove(requestKey, flight);
                flight = null;
            }
            if (flight == null) {
                if (FLIGHTS.size() >= MAX_TRACKED_REQUESTS) {
                    throw new IOException("Too many core requests are already active.");
                }
                flight = new Flight(requestKey, url, normalizedToken, clock, transport);
                flight.subscriberCount = 1;
                FLIGHTS.put(requestKey, flight);
                created = true;
            } else {
                flight.subscriberCount++;
            }
        }
        if (created) {
            submit(flight);
        }

        try {
            return awaitResult(flight, callerCancellation);
        } finally {
            release(flight);
        }
    }

    static void clear() {
        List<Flight> active = new ArrayList<>();
        synchronized (LOCK) {
            for (Flight flight : FLIGHTS.values()) {
                if (flight.state == State.RUNNING) {
                    flight.state = State.ABANDONED;
                    active.add(flight);
                    flight.completedSignal.countDown();
                }
            }
            FLIGHTS.clear();
        }
        for (Flight flight : active) {
            cancelTransport(flight);
        }
    }

    static int subscriberCountForTest(String url, String token) {
        synchronized (LOCK) {
            Flight flight = FLIGHTS.get(requestKey(url, token == null ? "" : token.trim()));
            return flight == null ? 0 : flight.subscriberCount;
        }
    }

    private static void submit(Flight flight) {
        synchronized (LOCK) {
            if (flight.state != State.RUNNING || flight.transportTask.isCancelled()) {
                return;
            }
        }
        try {
            TRANSPORT_EXECUTOR.execute(flight.transportTask);
        } catch (RejectedExecutionException exception) {
            complete(flight, null, exception, flight.clock.now());
        }
    }

    private static void runTransport(Flight flight) {
        synchronized (LOCK) {
            if (flight.state != State.RUNNING) {
                return;
            }
        }
        try {
            JSONObject data = flight.transport.load(
                    flight.url,
                    flight.token,
                    flight.transportCancellation
            );
            if (data == null) {
                throw new IOException("Core API returned no data.");
            }
            complete(flight, data.toString(), null, flight.clock.now());
        } catch (Throwable failure) {
            complete(flight, null, failure, flight.clock.now());
        }
    }

    private static void complete(
            Flight flight,
            String rawJson,
            Throwable failure,
            long completedAtElapsed
    ) {
        synchronized (LOCK) {
            completeLocked(flight, rawJson, failure, completedAtElapsed);
        }
    }

    private static void completeLocked(
            Flight flight,
            String rawJson,
            Throwable failure,
            long completedAtElapsed
    ) {
        if (flight.state != State.RUNNING) {
            return;
        }
        flight.rawJson = rawJson;
        flight.failure = failure;
        flight.completedAtElapsed = completedAtElapsed;
        flight.state = failure == null ? State.SUCCEEDED : State.FAILED;
        flight.completedSignal.countDown();
    }

    private static Result awaitResult(
            Flight flight,
            ApiClient.RequestCancellation callerCancellation
    ) throws IOException, JSONException {
        Thread waitingThread = Thread.currentThread();
        Runnable cancelListener = waitingThread::interrupt;
        ApiClient.RequestCancellation.CancelListenerRegistration cancelRegistration = null;
        if (callerCancellation != null) {
            cancelRegistration = callerCancellation.addCancelListener(cancelListener);
        }
        try {
            throwIfCallerCancelled(callerCancellation);
            boolean completed;
            try {
                completed = flight.completedSignal.await(WAIT_TIMEOUT_MS, TimeUnit.MILLISECONDS);
            } catch (InterruptedException exception) {
                Thread.currentThread().interrupt();
                InterruptedIOException cancelled = new InterruptedIOException(
                        "Core API request was cancelled."
                );
                cancelled.initCause(exception);
                throw cancelled;
            }
            throwIfCallerCancelled(callerCancellation);
            if (!completed) {
                throw new SocketTimeoutException("Core API shared request timed out.");
            }

            State state;
            String rawJson;
            Throwable failure;
            long completedAtElapsed;
            synchronized (LOCK) {
                state = flight.state;
                rawJson = flight.rawJson;
                failure = flight.failure;
                completedAtElapsed = flight.completedAtElapsed;
            }
            if (callerCancellation != null) {
                callerCancellation.removeCancelListener(cancelRegistration);
                cancelRegistration = null;
            }
            throwIfCallerCancelled(callerCancellation);
            if (state == State.ABANDONED) {
                throw new InterruptedIOException("Core API request was cancelled.");
            }
            if (failure != null) {
                throwFailure(failure);
            }
            if (state != State.SUCCEEDED || rawJson == null) {
                throw new IOException("Core API shared request did not complete.");
            }
            return new Result(rawJson, completedAtElapsed);
        } finally {
            if (callerCancellation != null && cancelRegistration != null) {
                callerCancellation.removeCancelListener(cancelRegistration);
            }
        }
    }

    private static void release(Flight flight) {
        boolean cancel = false;
        synchronized (LOCK) {
            if (flight.subscriberCount > 0) {
                flight.subscriberCount--;
            }
            if (flight.subscriberCount == 0) {
                if (flight.state == State.RUNNING) {
                    flight.state = State.ABANDONED;
                    FLIGHTS.remove(flight.requestKey, flight);
                    flight.completedSignal.countDown();
                    cancel = true;
                } else if (flight.state == State.FAILED
                        || (flight.state == State.SUCCEEDED
                        && flight.clock.now() - flight.completedAtElapsed > TERMINAL_GRACE_MS)) {
                    FLIGHTS.remove(flight.requestKey, flight);
                }
            }
        }
        if (cancel) {
            cancelTransport(flight);
        }
    }

    private static void cancelTransport(Flight flight) {
        flight.transportCancellation.cancel();
        FutureTask<Void> task = flight.transportTask;
        task.cancel(true);
        TRANSPORT_EXECUTOR.remove(task);
    }

    private static void pruneExpiredLocked(long nowElapsed) {
        Iterator<Map.Entry<String, Flight>> iterator = FLIGHTS.entrySet().iterator();
        while (iterator.hasNext()) {
            Flight flight = iterator.next().getValue();
            if (flight.subscriberCount == 0
                    && (flight.state == State.FAILED
                    || (flight.state == State.SUCCEEDED
                    && (nowElapsed < flight.completedAtElapsed
                    || nowElapsed - flight.completedAtElapsed > TERMINAL_GRACE_MS)))) {
                iterator.remove();
            }
        }
    }

    private static void throwIfCallerCancelled(
            ApiClient.RequestCancellation callerCancellation
    ) throws InterruptedIOException {
        if (Thread.currentThread().isInterrupted()
                || (callerCancellation != null && callerCancellation.isCancelled())) {
            throw new InterruptedIOException("Core API request was cancelled.");
        }
    }

    private static void throwFailure(Throwable failure) throws IOException, JSONException {
        if (failure instanceof IOException) {
            throw (IOException) failure;
        }
        if (failure instanceof JSONException) {
            throw (JSONException) failure;
        }
        if (failure instanceof RuntimeException) {
            throw (RuntimeException) failure;
        }
        if (failure instanceof Error) {
            throw (Error) failure;
        }
        IOException wrapped = new IOException("Core API shared request failed.");
        wrapped.initCause(failure);
        throw wrapped;
    }

    private static String requestKey(String url, String normalizedToken) {
        return "GET\n" + RecentCoreSnapshotCache.requestKey(url, normalizedToken);
    }

    private static ThreadPoolExecutor createTransportExecutor() {
        return new ThreadPoolExecutor(
                0,
                2,
                15L,
                TimeUnit.SECONDS,
                new SynchronousQueue<>(),
                runnable -> {
                    Thread thread = new Thread(runnable, "mooncen-core-network");
                    thread.setDaemon(true);
                    return thread;
                },
                new ThreadPoolExecutor.AbortPolicy()
        );
    }

    interface ElapsedClock {
        long now();
    }

    interface Transport {
        JSONObject load(
                String url,
                String token,
                ApiClient.RequestCancellation cancellation
        ) throws IOException, JSONException;
    }

    static final class Result {
        private final String rawJson;
        final long completedAtElapsed;

        private Result(String rawJson, long completedAtElapsed) {
            this.rawJson = rawJson;
            this.completedAtElapsed = completedAtElapsed;
        }

        JSONObject data() throws JSONException {
            return new JSONObject(rawJson);
        }
    }

    private enum State {
        RUNNING,
        SUCCEEDED,
        FAILED,
        ABANDONED;

        boolean isTerminal() {
            return this == SUCCEEDED || this == FAILED;
        }
    }

    private static final class Flight {
        final String requestKey;
        final String url;
        final String token;
        final ElapsedClock clock;
        final Transport transport;
        final ApiClient.RequestCancellation transportCancellation =
                new ApiClient.RequestCancellation();
        final java.util.concurrent.CountDownLatch completedSignal =
                new java.util.concurrent.CountDownLatch(1);
        State state = State.RUNNING;
        int subscriberCount;
        final FutureTask<Void> transportTask;
        String rawJson;
        Throwable failure;
        long completedAtElapsed;

        private Flight(
                String requestKey,
                String url,
                String token,
                ElapsedClock clock,
                Transport transport
        ) {
            this.requestKey = requestKey;
            this.url = url;
            this.token = token;
            this.clock = clock;
            this.transport = transport;
            this.transportTask = new FutureTask<>(() -> {
                runTransport(this);
                return null;
            });
        }
    }
}
