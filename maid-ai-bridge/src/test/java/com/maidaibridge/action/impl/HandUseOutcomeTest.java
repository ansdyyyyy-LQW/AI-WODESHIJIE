package com.maidaibridge.action.impl;

/** Runs without a fake Minecraft player and checks the three required result classes. */
public final class HandUseOutcomeTest {
    private static void require(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }

    public static void main(String[] args) {
        require(
                HandUseOutcome.duration(false, false, true, true) == HandUseOutcome.Value.SUCCESS,
                "an EntityMaid-compatible item with a real delta must succeed"
        );
        require(
                HandUseOutcome.duration(true, false, true, false) == HandUseOutcome.Value.PLAYER_CONTEXT_REQUIRED,
                "a Player-only item must be explicit"
        );
        require(
                HandUseOutcome.duration(false, false, true, false) == HandUseOutcome.Value.NO_EFFECT,
                "a completed call without an effect must not succeed"
        );
    }
}
