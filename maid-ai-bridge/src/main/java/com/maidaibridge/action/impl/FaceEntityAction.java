package com.maidaibridge.action.impl;

import com.maidaibridge.BridgeConfig;
import com.maidaibridge.action.AbstractMaidAction;
import com.maidaibridge.action.ActionContext;
import com.maidaibridge.controller.MotionPriority;
import net.minecraft.util.Mth;
import net.minecraft.world.entity.Entity;

import java.util.UUID;

public final class FaceEntityAction extends AbstractMaidAction {
    private final UUID targetId;
    private final boolean track;
    private final int durationTicks;
    private Entity target;
    private int stableTicks;

    public FaceEntityAction(String requestId, int timeout, UUID targetId, boolean track, int durationTicks) {
        super(requestId, "face_entity", Math.max(1, Math.min(timeout, durationTicks)));
        this.targetId = targetId;
        this.track = track;
        this.durationTicks = Math.max(1, durationTicks);
    }

    @Override
    protected void onStart(ActionContext context) {
        target = context.level().getEntity(targetId);
        int radius = BridgeConfig.ENTITY_OBSERVE_RADIUS.get();
        if (target == null || !target.isAlive()) {
            fail("TARGET_GONE");
            return;
        }
        if (context.maid().distanceToSqr(target) > radius * radius || !context.maid().hasLineOfSight(target)) {
            fail("TARGET_NOT_VISIBLE");
            return;
        }
        if (context.motion().acquire(id, MotionPriority.TASK, context.gameTick()).isEmpty()) {
            fail("MOTION_BUSY");
            return;
        }
        context.motion().halt(id, context.maid());
    }

    @Override
    protected void onTick(ActionContext context) {
        if (target == null || !target.isAlive()) {
            fail("TARGET_GONE");
            context.motion().release(id, context.maid());
            return;
        }
        if (!context.maid().hasLineOfSight(target)) {
            fail("TARGET_NOT_VISIBLE");
            context.motion().release(id, context.maid());
            return;
        }
        context.maid().getLookControl().setLookAt(target, 30, 30);
        double dx = target.getX() - context.maid().getX();
        double dz = target.getZ() - context.maid().getZ();
        float targetYaw = (float) (Mth.atan2(dz, dx) * 180.0 / Math.PI) - 90.0F;
        float error = Math.abs(Mth.wrapDegrees(targetYaw - context.maid().getYHeadRot()));
        data.addProperty("angle_error", error);
        if (error <= 5) stableTicks++; else stableTicks = 0;
        if ((!track && stableTicks >= 2) || (track && context.gameTick() - startTick >= durationTicks)) {
            context.motion().release(id, context.maid());
            succeed(track ? "TRACKING_FINISHED" : "FACING_ENTITY");
        }
    }

    @Override
    protected void onStop(ActionContext context) {
        context.motion().release(id, context.maid());
    }
}
