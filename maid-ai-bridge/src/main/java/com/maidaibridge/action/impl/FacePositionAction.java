package com.maidaibridge.action.impl;

import com.maidaibridge.action.AbstractMaidAction;
import com.maidaibridge.action.ActionContext;
import com.maidaibridge.controller.MotionPriority;
import net.minecraft.util.Mth;

/** Finite facing adjustment that owns the body through MotionArbiter. */
public final class FacePositionAction extends AbstractMaidAction {
    private final double x;
    private final double y;
    private final double z;
    private final double tolerance;
    private int stableTicks;

    public FacePositionAction(String requestId, int timeout, double x, double y, double z, double tolerance) {
        super(requestId, "face_position", timeout);
        this.x = x;
        this.y = y;
        this.z = z;
        this.tolerance = Math.max(.5, Math.min(45, tolerance));
    }

    @Override
    protected void onStart(ActionContext context) {
        if (context.motion().acquire(id, MotionPriority.TASK, context.gameTick()).isEmpty()) {
            fail("MOTION_BUSY");
            return;
        }
        context.motion().halt(id, context.maid());
    }

    @Override
    protected void onTick(ActionContext context) {
        context.maid().getLookControl().setLookAt(x, y, z, 30, 30);
        double dx = x - context.maid().getX();
        double dz = z - context.maid().getZ();
        float targetYaw = (float) (Mth.atan2(dz, dx) * 180.0 / Math.PI) - 90.0F;
        float error = Math.abs(Mth.wrapDegrees(targetYaw - context.maid().getYHeadRot()));
        data.addProperty("angle_error", error);
        if (error <= tolerance) stableTicks++; else stableTicks = 0;
        if (stableTicks >= 2) {
            context.motion().release(id, context.maid());
            succeed("FACING_POSITION");
        }
    }

    @Override
    protected void onStop(ActionContext context) {
        context.motion().release(id, context.maid());
    }
}
