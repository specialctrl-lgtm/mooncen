package com.mooncen.monitor;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

import java.util.Arrays;
import java.util.LinkedHashSet;
import java.util.Set;

public class ProblemStateTest {
    @Test
    public void unresolvedCoreStatusRetainsPreviousCriticalProblem() {
        Set<String> previous = keys("core:backend");

        assertEquals(
                keys("core:backend"),
                ProblemState.reconcileCore(previous, keys(), keys())
        );
    }

    @Test
    public void confirmedHealthyRecoversWhileAnotherUnresolvedProblemRemains() {
        Set<String> previous = keys("core:backend", "core:crawler");

        assertEquals(
                keys("core:crawler"),
                ProblemState.reconcileCore(previous, keys(), keys("core:backend"))
        );
    }

    @Test
    public void confirmedCriticalIsAddedWithoutResolvingUnknownKeys() {
        Set<String> previous = keys("core:crawler");

        assertEquals(
                keys("core:crawler", "core:database"),
                ProblemState.reconcileCore(
                        previous,
                        keys("core:database"),
                        keys("core:frontend")
                )
        );
    }

    private static Set<String> keys(String... values) {
        return new LinkedHashSet<>(Arrays.asList(values));
    }
}
