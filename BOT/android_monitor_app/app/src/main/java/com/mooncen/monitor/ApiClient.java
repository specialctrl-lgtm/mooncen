package com.mooncen.monitor;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InterruptedIOException;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.SocketTimeoutException;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.ScheduledThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

public final class ApiClient {
    private static final int CONNECT_TIMEOUT_MS = 7_000;
    private static final int GET_TIMEOUT_MS = 10_000;
    private static final int POST_TIMEOUT_MS = 30_000;
    private static final int GET_TOTAL_TIMEOUT_MS = 20_000;
    private static final int POST_TOTAL_TIMEOUT_MS = 60_000;
    private static final int MAX_ERROR_BODY_LENGTH = 500;
    private static final ScheduledThreadPoolExecutor DEADLINE_EXECUTOR = createDeadlineExecutor();

    private ApiClient() {
    }

    public static JSONObject getJson(String url, String token) throws IOException, JSONException {
        return request("GET", url, token, null, GET_TIMEOUT_MS, GET_TOTAL_TIMEOUT_MS, null);
    }

    static JSONObject getJson(
            String url,
            String token,
            RequestCancellation cancellation
    ) throws IOException, JSONException {
        return request(
                "GET",
                url,
                token,
                null,
                GET_TIMEOUT_MS,
                GET_TOTAL_TIMEOUT_MS,
                cancellation
        );
    }

    public static JSONObject postJson(String url, String token, JSONObject payload) throws IOException, JSONException {
        return postJson(url, token, payload, null);
    }

    static JSONObject postJson(
            String url,
            String token,
            JSONObject payload,
            RequestCancellation cancellation
    ) throws IOException, JSONException {
        return request(
                "POST",
                url,
                token,
                payload,
                POST_TIMEOUT_MS,
                POST_TOTAL_TIMEOUT_MS,
                cancellation
        );
    }

    private static JSONObject request(
            String method,
            String urlText,
            String token,
            JSONObject payload,
            int readTimeoutMs,
            int totalTimeoutMs,
            RequestCancellation cancellation
    ) throws IOException, JSONException {
        HttpURLConnection connection = null;
        ScheduledFuture<?> deadlineTask = null;
        AtomicBoolean deadlineTriggered = new AtomicBoolean(false);
        long deadlineNanos = System.nanoTime() + totalTimeoutMs * 1_000_000L;
        try {
            throwIfCancelled(cancellation);
            throwIfDeadlineExceeded(deadlineNanos);
            URL url = new URL(urlText);
            String protocol = url.getProtocol();
            if (!"https".equalsIgnoreCase(protocol)) {
                throw new IOException("API 주소는 HTTPS만 사용할 수 있습니다.");
            }
            if (url.getUserInfo() != null || url.getQuery() != null || url.getRef() != null) {
                throw new IOException(
                        "API 주소에 사용자 정보, 쿼리 문자열 또는 fragment를 포함할 수 없습니다."
                );
            }

            connection = (HttpURLConnection) url.openConnection();
            if (cancellation != null) {
                cancellation.attach(connection);
            }
            HttpURLConnection deadlineConnection = connection;
            long remainingNanos = Math.max(1L, deadlineNanos - System.nanoTime());
            deadlineTask = DEADLINE_EXECUTOR.schedule(
                    () -> {
                        deadlineTriggered.set(true);
                        deadlineConnection.disconnect();
                    },
                    remainingNanos,
                    TimeUnit.NANOSECONDS
            );
            connection.setInstanceFollowRedirects(false);
            connection.setConnectTimeout(CONNECT_TIMEOUT_MS);
            connection.setReadTimeout(readTimeoutMs);
            connection.setRequestMethod(method);
            connection.setRequestProperty("Accept", "application/json");
            if (token != null && !token.trim().isEmpty()) {
                connection.setRequestProperty("X-App-Token", token.trim());
            }

            if (payload != null) {
                throwIfCancelled(cancellation);
                throwIfDeadlineExceeded(deadlineNanos);
                byte[] payloadBytes = payload.toString().getBytes(StandardCharsets.UTF_8);
                connection.setDoOutput(true);
                connection.setFixedLengthStreamingMode(payloadBytes.length);
                connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
                try (OutputStream output = connection.getOutputStream()) {
                    output.write(payloadBytes);
                }
                throwIfCancelled(cancellation);
                throwIfDeadlineExceeded(deadlineNanos);
            }

            throwIfCancelled(cancellation);
            throwIfDeadlineExceeded(deadlineNanos);
            connection.setReadTimeout(remainingReadTimeoutMs(deadlineNanos, readTimeoutMs));
            int statusCode = connection.getResponseCode();
            throwIfCancelled(cancellation);
            throwIfDeadlineExceeded(deadlineNanos);
            boolean successful = statusCode >= 200 && statusCode < 300;
            InputStream stream = successful ? connection.getInputStream() : connection.getErrorStream();
            String body = readBody(
                    stream,
                    connection,
                    readTimeoutMs,
                    cancellation,
                    deadlineNanos
            );
            throwIfCancelled(cancellation);
            throwIfDeadlineExceeded(deadlineNanos);

            if (!successful) {
                throw new ApiException(statusCode, formatHttpError(statusCode, body));
            }
            if (body.trim().isEmpty()) {
                throw new IOException("서버 응답이 비어 있습니다.");
            }

            try {
                JSONObject result = new JSONObject(body);
                throwIfCancelled(cancellation);
                throwIfDeadlineExceeded(deadlineNanos);
                return result;
            } catch (JSONException exception) {
                throw new JSONException("JSON 응답을 해석할 수 없습니다: " + abbreviate(body));
            }
        } catch (IOException exception) {
            if (deadlineTriggered.get() && !(exception instanceof SocketTimeoutException)) {
                SocketTimeoutException timeout =
                        new SocketTimeoutException("API 요청의 전체 제한 시간을 초과했습니다.");
                timeout.initCause(exception);
                throw timeout;
            }
            throw exception;
        } finally {
            if (deadlineTask != null) {
                deadlineTask.cancel(false);
            }
            if (connection != null) {
                if (cancellation != null) {
                    cancellation.detach(connection);
                }
                connection.disconnect();
            }
        }
    }

    private static ScheduledThreadPoolExecutor createDeadlineExecutor() {
        ScheduledThreadPoolExecutor executor = new ScheduledThreadPoolExecutor(1, runnable -> {
            Thread thread = new Thread(runnable, "mooncen-api-deadline");
            thread.setDaemon(true);
            return thread;
        });
        executor.setRemoveOnCancelPolicy(true);
        return executor;
    }

    private static String readBody(
            InputStream stream,
            HttpURLConnection connection,
            int readTimeoutMs,
            RequestCancellation cancellation,
            long deadlineNanos
    ) throws IOException {
        if (stream == null) {
            return "";
        }
        try (InputStream input = stream;
             ByteArrayOutputStream body = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[8 * 1024];
            int totalBytes = 0;
            while (true) {
                throwIfCancelled(cancellation);
                throwIfDeadlineExceeded(deadlineNanos);
                connection.setReadTimeout(remainingReadTimeoutMs(deadlineNanos, readTimeoutMs));
                int read = input.read(buffer);
                throwIfCancelled(cancellation);
                throwIfDeadlineExceeded(deadlineNanos);
                if (read < 0) {
                    break;
                }
                if (totalBytes > PowerPolicy.MAX_RESPONSE_BODY_BYTES - read) {
                    throw new IOException("API 응답이 허용 크기 512 KiB를 초과했습니다.");
                }
                body.write(buffer, 0, read);
                totalBytes += read;
            }
            return new String(body.toByteArray(), StandardCharsets.UTF_8);
        }
    }

    private static void throwIfCancelled(RequestCancellation cancellation)
            throws InterruptedIOException {
        if (Thread.currentThread().isInterrupted()
                || (cancellation != null && cancellation.isCancelled())) {
            throw new InterruptedIOException("API 요청이 취소되었습니다.");
        }
    }

    private static void throwIfDeadlineExceeded(long deadlineNanos) throws SocketTimeoutException {
        if (System.nanoTime() - deadlineNanos >= 0L) {
            throw new SocketTimeoutException("API 요청의 전체 제한 시간을 초과했습니다.");
        }
    }

    private static int remainingReadTimeoutMs(long deadlineNanos, int maximumMs)
            throws SocketTimeoutException {
        long remainingNanos = deadlineNanos - System.nanoTime();
        if (remainingNanos <= 0L) {
            throw new SocketTimeoutException("API 요청의 전체 제한 시간을 초과했습니다.");
        }
        long remainingMs = (remainingNanos + 999_999L) / 1_000_000L;
        return (int) Math.max(1L, Math.min(maximumMs, remainingMs));
    }

    static final class RequestCancellation {
        private boolean cancelled;
        private HttpURLConnection connection;
        private final Set<CancelListenerRegistration> cancelListeners =
                new LinkedHashSet<>();

        void attach(HttpURLConnection value) {
            boolean cancelNow;
            synchronized (this) {
                cancelNow = cancelled;
                if (!cancelNow) {
                    connection = value;
                }
            }
            if (cancelNow) {
                value.disconnect();
            }
        }

        void detach(HttpURLConnection value) {
            synchronized (this) {
                if (connection == value) {
                    connection = null;
                }
            }
        }

        void cancel() {
            HttpURLConnection active;
            List<CancelListenerRegistration> listeners;
            synchronized (this) {
                if (cancelled) {
                    return;
                }
                cancelled = true;
                active = connection;
                connection = null;
                listeners = new ArrayList<>(cancelListeners);
                cancelListeners.clear();
            }
            for (CancelListenerRegistration listener : listeners) {
                try {
                    listener.fire();
                } catch (RuntimeException ignored) {
                    // Cancellation must continue even if a listener has already torn down.
                }
            }
            if (active != null) {
                active.disconnect();
            }
        }

        CancelListenerRegistration addCancelListener(Runnable listener) {
            CancelListenerRegistration registration =
                    new CancelListenerRegistration(listener);
            boolean notifyNow;
            synchronized (this) {
                notifyNow = cancelled;
                if (!notifyNow) {
                    cancelListeners.add(registration);
                }
            }
            if (notifyNow) {
                registration.fire();
            }
            return registration;
        }

        void removeCancelListener(CancelListenerRegistration registration) {
            synchronized (this) {
                cancelListeners.remove(registration);
            }
            registration.close();
        }

        boolean isCancelled() {
            synchronized (this) {
                return cancelled;
            }
        }

        /**
         * Serializes firing and removal so close() is a barrier against a late callback.
         */
        static final class CancelListenerRegistration implements AutoCloseable {
            private final Runnable listener;
            private boolean active = true;

            private CancelListenerRegistration(Runnable listener) {
                this.listener = listener;
            }

            private synchronized void fire() {
                if (!active) {
                    return;
                }
                active = false;
                listener.run();
            }

            @Override
            public synchronized void close() {
                active = false;
            }
        }
    }

    private static String formatHttpError(int statusCode, String body) {
        String detail = "";
        if (body != null && !body.trim().isEmpty()) {
            try {
                JSONObject json = new JSONObject(body);
                detail = firstNonEmpty(
                        json.optString("error", ""),
                        json.optString("message", ""),
                        json.optString("detail", "")
                );
            } catch (JSONException ignored) {
                detail = body.replaceAll("\\s+", " ").trim();
            }
        }
        String prefix = "HTTP " + statusCode;
        return detail.isEmpty() ? prefix : prefix + " · " + abbreviate(detail);
    }

    private static String firstNonEmpty(String... values) {
        for (String value : values) {
            if (value != null && !value.trim().isEmpty()) {
                return value.trim();
            }
        }
        return "";
    }

    private static String abbreviate(String value) {
        String compact = value == null ? "" : value.replaceAll("\\s+", " ").trim();
        if (compact.length() <= MAX_ERROR_BODY_LENGTH) {
            return compact;
        }
        return compact.substring(0, MAX_ERROR_BODY_LENGTH) + "…";
    }

    public static final class ApiException extends IOException {
        private final int statusCode;

        ApiException(int statusCode, String message) {
            super(message);
            this.statusCode = statusCode;
        }

        public int getStatusCode() {
            return statusCode;
        }
    }
}
