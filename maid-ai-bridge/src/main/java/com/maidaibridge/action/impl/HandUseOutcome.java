package com.maidaibridge.action.impl;

/** Small evidence gate shared by hand-use execution and its regression check. */
final class HandUseOutcome {
    enum Value {
        SUCCESS,
        UNSUPPORTED,
        PLAYER_CONTEXT_REQUIRED,
        NO_EFFECT,
        FAILED
    }

    private HandUseOutcome() { }

    static Value immediate(boolean playerContextRequired, boolean failed,
                           boolean minecraftAccepted, boolean observableChange) {
        if (playerContextRequired) return Value.PLAYER_CONTEXT_REQUIRED;
        if (failed) return Value.FAILED;
        if (minecraftAccepted || observableChange) return Value.SUCCESS;
        return Value.UNSUPPORTED;
    }

    static Value duration(boolean playerContextRequired, boolean failed,
                          boolean attempted, boolean observableChange) {
        if (playerContextRequired) return Value.PLAYER_CONTEXT_REQUIRED;
        if (failed) return Value.FAILED;
        if (observableChange) return Value.SUCCESS;
        return attempted ? Value.NO_EFFECT : Value.UNSUPPORTED;
    }
}
