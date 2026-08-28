package com.maidaibridge.action.impl;

import com.maidaibridge.action.AbstractMaidAction;
import com.maidaibridge.action.ActionContext;

public final class WaitAction extends AbstractMaidAction {
    private final int durationTicks;

    public WaitAction(String requestId, int timeout, int durationTicks) {
        super(requestId, "wait", timeout);
        this.durationTicks = Math.max(1, durationTicks);
    }

    @Override
    protected void onTick(ActionContext context) {
        long elapsed = context.gameTick() - startTick;
        data.addProperty("elapsed_ticks", elapsed);
        if (elapsed >= durationTicks) succeed("WAIT_FINISHED");
    }
}
