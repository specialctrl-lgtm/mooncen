package com.mooncen.monitor;

import java.util.LinkedHashSet;
import java.util.Set;

final class ProblemState {
    private ProblemState() {
    }

    static Set<String> reconcileCore(
            Set<String> previousKeys,
            Set<String> criticalKeys,
            Set<String> healthyKeys
    ) {
        // Keys absent from both sets are unresolved. Keep their last confirmed
        // critical state so warning/unknown observations never emit a false recovery.
        Set<String> result = new LinkedHashSet<>(previousKeys);
        result.removeAll(healthyKeys);
        result.addAll(criticalKeys);
        return result;
    }
}
