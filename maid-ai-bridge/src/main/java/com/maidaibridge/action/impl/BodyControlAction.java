package com.maidaibridge.action.impl;

import com.maidaibridge.action.AbstractMaidAction;
import com.maidaibridge.action.ActionContext;
import com.maidaibridge.controller.MotionPriority;

/** Short, cancellable body-state action under the same movement lease as navigation. */
public final class BodyControlAction extends AbstractMaidAction {
    public enum Kind { JUMP, SNEAK_ON, SNEAK_OFF }

    private final Kind kind;

    public BodyControlAction(String requestId, String type, int timeout, Kind kind) {
        super(requestId, type, timeout);
        this.kind = kind;
    }

    @Override
    protected void onStart(ActionContext context) {
        if (context.motion().acquire(id, MotionPriority.TASK, context.gameTick()).isEmpty()) {
            fail("MOTION_BUSY");
            return;
        }
        context.motion().halt(id, context.maid());
        if (kind == Kind.JUMP) {
            context.maid().getJumpControl().jump();
        } else {
            context.maid().setShiftKeyDown(kind == Kind.SNEAK_ON);
        }
    }

    @Override
    protected void onTick(ActionContext context) {
        if (context.gameTick() - startTick < 1) return;
        data.addProperty("sneaking", context.maid().isShiftKeyDown());
        context.motion().release(id, context.maid());
        succeed(kind == Kind.JUMP ? "JUMPED" : kind == Kind.SNEAK_ON ? "SNEAK_ENABLED" : "SNEAK_DISABLED");
    }

    @Override
    protected void onStop(ActionContext context) {
        if (kind == Kind.SNEAK_ON && state != com.maidaibridge.action.ActionState.SUCCESS) {
            context.maid().setShiftKeyDown(false);
        }
        context.motion().release(id, context.maid());
    }
}
