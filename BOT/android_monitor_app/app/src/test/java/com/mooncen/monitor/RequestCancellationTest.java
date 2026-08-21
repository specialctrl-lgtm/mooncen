package com.mooncen.monitor;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

public class RequestCancellationTest {
    @Test
    public void removingSnapshottedListenerPreventsLateCallback() throws Exception {
        ApiClient.RequestCancellation cancellation = new ApiClient.RequestCancellation();
        CountDownLatch firstListenerStarted = new CountDownLatch(1);
        CountDownLatch releaseFirstListener = new CountDownLatch(1);
        AtomicInteger lateCallbacks = new AtomicInteger();

        cancellation.addCancelListener(() -> {
            firstListenerStarted.countDown();
            try {
                releaseFirstListener.await(2, TimeUnit.SECONDS);
            } catch (InterruptedException exception) {
                Thread.currentThread().interrupt();
            }
        });
        ApiClient.RequestCancellation.CancelListenerRegistration registration =
                cancellation.addCancelListener(lateCallbacks::incrementAndGet);

        Thread cancelThread = new Thread(cancellation::cancel, "cancellation-test");
        cancelThread.start();
        assertTrue(firstListenerStarted.await(2, TimeUnit.SECONDS));

        cancellation.removeCancelListener(registration);
        releaseFirstListener.countDown();
        cancelThread.join(2_000L);

        assertFalse(cancelThread.isAlive());
        assertEquals(0, lateCallbacks.get());
    }

    @Test
    public void removingFiringListenerWaitsForCallbackCompletion() throws Exception {
        ApiClient.RequestCancellation cancellation = new ApiClient.RequestCancellation();
        CountDownLatch listenerStarted = new CountDownLatch(1);
        CountDownLatch releaseListener = new CountDownLatch(1);
        CountDownLatch listenerCompleted = new CountDownLatch(1);
        CountDownLatch removalCompleted = new CountDownLatch(1);
        ApiClient.RequestCancellation.CancelListenerRegistration registration =
                cancellation.addCancelListener(() -> {
                    listenerStarted.countDown();
                    try {
                        releaseListener.await(5, TimeUnit.SECONDS);
                    } catch (InterruptedException exception) {
                        Thread.currentThread().interrupt();
                    } finally {
                        listenerCompleted.countDown();
                    }
                });

        Thread cancelThread = new Thread(cancellation::cancel, "cancellation-fire-test");
        Thread removeThread = new Thread(() -> {
            cancellation.removeCancelListener(registration);
            removalCompleted.countDown();
        }, "cancellation-remove-test");
        cancelThread.start();
        assertTrue(listenerStarted.await(2, TimeUnit.SECONDS));
        removeThread.start();

        assertFalse(removalCompleted.await(100, TimeUnit.MILLISECONDS));
        releaseListener.countDown();
        assertTrue(removalCompleted.await(2, TimeUnit.SECONDS));
        assertEquals(0L, listenerCompleted.getCount());

        cancelThread.join(2_000L);
        removeThread.join(2_000L);
        assertFalse(cancelThread.isAlive());
        assertFalse(removeThread.isAlive());
    }
}
