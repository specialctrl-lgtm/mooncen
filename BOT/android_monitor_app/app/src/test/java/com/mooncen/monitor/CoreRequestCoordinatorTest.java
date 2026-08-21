package com.mooncen.monitor;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import org.json.JSONObject;
import org.junit.After;
import org.junit.Test;

import java.io.IOException;
import java.io.InterruptedIOException;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

public class CoreRequestCoordinatorTest {
    private static final String URL = "https://monitor.example/core";
    private static final String TOKEN = "token";

    @After
    public void clearCoordinator() {
        CoreRequestCoordinator.clear();
    }

    @Test
    public void identicalConcurrentRequestsUseOneTransportAndOneTimestamp() throws Exception {
        ExecutorService callers = Executors.newFixedThreadPool(2);
        CountDownLatch transportStarted = new CountDownLatch(1);
        CountDownLatch releaseTransport = new CountDownLatch(1);
        AtomicInteger transportCalls = new AtomicInteger();
        CoreRequestCoordinator.Transport transport = (url, token, cancellation) -> {
            transportCalls.incrementAndGet();
            transportStarted.countDown();
            await(releaseTransport);
            return new JSONObject().put("value", "shared");
        };

        try {
            Future<CoreRequestCoordinator.Result> first = callers.submit(() ->
                    request(new ApiClient.RequestCancellation(), transport, 1_234L));
            assertTrue(transportStarted.await(2, TimeUnit.SECONDS));
            Future<CoreRequestCoordinator.Result> second = callers.submit(() ->
                    request(new ApiClient.RequestCancellation(), transport, 1_234L));
            awaitSubscriberCount(2);

            releaseTransport.countDown();
            CoreRequestCoordinator.Result firstResult = first.get(2, TimeUnit.SECONDS);
            CoreRequestCoordinator.Result secondResult = second.get(2, TimeUnit.SECONDS);

            assertEquals(1, transportCalls.get());
            assertEquals("shared", firstResult.data().getString("value"));
            assertEquals("shared", secondResult.data().getString("value"));
            assertEquals(1_234L, firstResult.completedAtElapsed);
            assertEquals(firstResult.completedAtElapsed, secondResult.completedAtElapsed);
        } finally {
            releaseTransport.countDown();
            callers.shutdownNow();
        }
    }

    @Test
    public void cancellingOneSubscriberLeavesTransportForTheOtherSubscriber() throws Exception {
        ExecutorService callers = Executors.newFixedThreadPool(2);
        CountDownLatch transportStarted = new CountDownLatch(1);
        CountDownLatch releaseTransport = new CountDownLatch(1);
        AtomicReference<ApiClient.RequestCancellation> transportCancellation =
                new AtomicReference<>();
        AtomicInteger transportCalls = new AtomicInteger();
        CoreRequestCoordinator.Transport transport = (url, token, cancellation) -> {
            transportCalls.incrementAndGet();
            transportCancellation.set(cancellation);
            transportStarted.countDown();
            await(releaseTransport);
            return new JSONObject().put("value", "remaining");
        };
        ApiClient.RequestCancellation firstCancellation = new ApiClient.RequestCancellation();
        ApiClient.RequestCancellation secondCancellation = new ApiClient.RequestCancellation();

        try {
            Future<CoreRequestCoordinator.Result> first = callers.submit(() ->
                    request(firstCancellation, transport, 2_000L));
            assertTrue(transportStarted.await(2, TimeUnit.SECONDS));
            Future<CoreRequestCoordinator.Result> second = callers.submit(() ->
                    request(secondCancellation, transport, 2_000L));
            awaitSubscriberCount(2);

            firstCancellation.cancel();
            assertInterrupted(first);
            awaitSubscriberCount(1);
            assertNotNull(transportCancellation.get());
            assertFalse(transportCancellation.get().isCancelled());

            releaseTransport.countDown();
            CoreRequestCoordinator.Result remaining = second.get(2, TimeUnit.SECONDS);
            assertEquals("remaining", remaining.data().getString("value"));
            assertEquals(1, transportCalls.get());
        } finally {
            releaseTransport.countDown();
            callers.shutdownNow();
        }
    }

    @Test
    public void cancellingLastSubscriberCancelsUnderlyingTransportOnce() throws Exception {
        ExecutorService callers = Executors.newFixedThreadPool(2);
        CountDownLatch transportStarted = new CountDownLatch(1);
        CountDownLatch transportCancelled = new CountDownLatch(1);
        CountDownLatch neverRelease = new CountDownLatch(1);
        AtomicInteger cancellationCallbacks = new AtomicInteger();
        CoreRequestCoordinator.Transport transport = (url, token, cancellation) -> {
            cancellation.addCancelListener(() -> {
                cancellationCallbacks.incrementAndGet();
                transportCancelled.countDown();
            });
            transportStarted.countDown();
            await(neverRelease);
            throw new IOException("unexpected release");
        };
        ApiClient.RequestCancellation firstCancellation = new ApiClient.RequestCancellation();
        ApiClient.RequestCancellation secondCancellation = new ApiClient.RequestCancellation();

        try {
            Future<CoreRequestCoordinator.Result> first = callers.submit(() ->
                    request(firstCancellation, transport, 3_000L));
            assertTrue(transportStarted.await(2, TimeUnit.SECONDS));
            Future<CoreRequestCoordinator.Result> second = callers.submit(() ->
                    request(secondCancellation, transport, 3_000L));
            awaitSubscriberCount(2);

            firstCancellation.cancel();
            assertInterrupted(first);
            assertFalse(transportCancelled.await(100, TimeUnit.MILLISECONDS));

            secondCancellation.cancel();
            assertInterrupted(second);
            assertTrue(transportCancelled.await(2, TimeUnit.SECONDS));
            assertEquals(1, cancellationCallbacks.get());
        } finally {
            neverRelease.countDown();
            callers.shutdownNow();
        }
    }

    @Test
    public void sharedTransportFailureIsNotRetriedPerSubscriber() throws Exception {
        ExecutorService callers = Executors.newFixedThreadPool(2);
        CountDownLatch transportStarted = new CountDownLatch(1);
        CountDownLatch releaseTransport = new CountDownLatch(1);
        AtomicInteger transportCalls = new AtomicInteger();
        CoreRequestCoordinator.Transport transport = (url, token, cancellation) -> {
            transportCalls.incrementAndGet();
            transportStarted.countDown();
            await(releaseTransport);
            throw new IOException("shared failure");
        };

        try {
            Future<CoreRequestCoordinator.Result> first = callers.submit(() ->
                    request(new ApiClient.RequestCancellation(), transport, 4_000L));
            assertTrue(transportStarted.await(2, TimeUnit.SECONDS));
            Future<CoreRequestCoordinator.Result> second = callers.submit(() ->
                    request(new ApiClient.RequestCancellation(), transport, 4_000L));
            awaitSubscriberCount(2);

            releaseTransport.countDown();
            assertIOException(first, "shared failure");
            assertIOException(second, "shared failure");
            assertEquals(1, transportCalls.get());
        } finally {
            releaseTransport.countDown();
            callers.shutdownNow();
        }
    }

    @Test
    public void differentTokensNeverShareTransport() throws Exception {
        ExecutorService callers = Executors.newFixedThreadPool(2);
        CountDownLatch transportsStarted = new CountDownLatch(2);
        CountDownLatch releaseTransports = new CountDownLatch(1);
        AtomicInteger transportCalls = new AtomicInteger();
        CoreRequestCoordinator.Transport transport = (url, token, cancellation) -> {
            transportCalls.incrementAndGet();
            transportsStarted.countDown();
            await(releaseTransports);
            return new JSONObject().put("token", token);
        };

        try {
            Future<CoreRequestCoordinator.Result> first = callers.submit(() ->
                    CoreRequestCoordinator.getJson(
                            URL,
                            "token-a",
                            new ApiClient.RequestCancellation(),
                            () -> 5_000L,
                            transport
                    ));
            Future<CoreRequestCoordinator.Result> second = callers.submit(() ->
                    CoreRequestCoordinator.getJson(
                            URL,
                            "token-b",
                            new ApiClient.RequestCancellation(),
                            () -> 5_000L,
                            transport
                    ));
            assertTrue(transportsStarted.await(2, TimeUnit.SECONDS));
            releaseTransports.countDown();

            assertEquals("token-a", first.get(2, TimeUnit.SECONDS).data().getString("token"));
            assertEquals("token-b", second.get(2, TimeUnit.SECONDS).data().getString("token"));
            assertEquals(2, transportCalls.get());
        } finally {
            releaseTransports.countDown();
            callers.shutdownNow();
        }
    }

    @Test
    public void completedResultBridgesShortCachePublicationGap() throws Exception {
        AtomicInteger transportCalls = new AtomicInteger();
        CoreRequestCoordinator.Transport transport = (url, token, cancellation) -> {
            transportCalls.incrementAndGet();
            return new JSONObject().put("value", "completed");
        };

        CoreRequestCoordinator.Result first = request(
                new ApiClient.RequestCancellation(),
                transport,
                6_000L
        );
        CoreRequestCoordinator.Result second = request(
                new ApiClient.RequestCancellation(),
                transport,
                6_000L
        );

        assertEquals("completed", first.data().getString("value"));
        assertEquals("completed", second.data().getString("value"));
        assertEquals(1, transportCalls.get());
        assertEquals(first.completedAtElapsed, second.completedAtElapsed);
    }

    @Test
    public void explicitRefreshBypassesCompletedSuccess() throws Exception {
        AtomicInteger transportCalls = new AtomicInteger();
        CoreRequestCoordinator.Transport transport = (url, token, cancellation) ->
                new JSONObject().put("call", transportCalls.incrementAndGet());

        CoreRequestCoordinator.Result first = request(
                new ApiClient.RequestCancellation(),
                transport,
                7_000L
        );
        CoreRequestCoordinator.Result second = CoreRequestCoordinator.getJson(
                URL,
                TOKEN,
                new ApiClient.RequestCancellation(),
                false,
                () -> 7_001L,
                transport
        );

        assertEquals(1, first.data().getInt("call"));
        assertEquals(2, second.data().getInt("call"));
        assertEquals(2, transportCalls.get());
    }

    @Test
    public void completedFailureIsNeverReused() throws Exception {
        AtomicInteger transportCalls = new AtomicInteger();
        CoreRequestCoordinator.Transport transport = (url, token, cancellation) -> {
            if (transportCalls.incrementAndGet() == 1) {
                throw new IOException("first failure");
            }
            return new JSONObject().put("value", "recovered");
        };

        try {
            request(new ApiClient.RequestCancellation(), transport, 8_000L);
            fail("Expected the first request to fail");
        } catch (IOException exception) {
            assertEquals("first failure", exception.getMessage());
        }
        CoreRequestCoordinator.Result recovered = request(
                new ApiClient.RequestCancellation(),
                transport,
                8_001L
        );

        assertEquals("recovered", recovered.data().getString("value"));
        assertEquals(2, transportCalls.get());
    }

    private static CoreRequestCoordinator.Result request(
            ApiClient.RequestCancellation cancellation,
            CoreRequestCoordinator.Transport transport,
            long completedAtElapsed
    ) throws Exception {
        return CoreRequestCoordinator.getJson(
                URL,
                TOKEN,
                cancellation,
                () -> completedAtElapsed,
                transport
        );
    }

    private static void awaitSubscriberCount(int expected) throws Exception {
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(2);
        while (System.nanoTime() < deadline) {
            if (CoreRequestCoordinator.subscriberCountForTest(URL, TOKEN) == expected) {
                return;
            }
            Thread.sleep(5L);
        }
        fail("Expected " + expected + " subscribers but found "
                + CoreRequestCoordinator.subscriberCountForTest(URL, TOKEN));
    }

    private static void assertInterrupted(Future<?> future) throws Exception {
        try {
            future.get(2, TimeUnit.SECONDS);
            fail("Expected an interrupted request");
        } catch (ExecutionException exception) {
            assertTrue(exception.getCause() instanceof InterruptedIOException);
        }
    }

    private static void assertIOException(Future<?> future, String message) throws Exception {
        try {
            future.get(2, TimeUnit.SECONDS);
            fail("Expected a shared transport failure");
        } catch (ExecutionException exception) {
            assertTrue(exception.getCause() instanceof IOException);
            assertEquals(message, exception.getCause().getMessage());
        }
    }

    private static void await(CountDownLatch latch) throws IOException {
        try {
            if (!latch.await(5, TimeUnit.SECONDS)) {
                throw new IOException("test transport wait timed out");
            }
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
            InterruptedIOException cancelled = new InterruptedIOException("test transport interrupted");
            cancelled.initCause(exception);
            throw cancelled;
        }
    }
}
