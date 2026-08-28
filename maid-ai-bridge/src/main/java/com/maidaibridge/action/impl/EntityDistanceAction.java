package com.maidaibridge.action.impl;

import com.maidaibridge.BridgeConfig;
import com.maidaibridge.action.AbstractMaidAction;
import com.maidaibridge.action.ActionContext;
import com.maidaibridge.controller.MotionPriority;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.phys.Vec3;

import java.util.UUID;

/** Bridge-owned distance controller; the AI is not involved in per-tick correction. */
public final class EntityDistanceAction extends AbstractMaidAction {
    public enum Mode { APPROACH, AWAY, MAINTAIN }

    private final UUID targetId;
    private final Mode mode;
    private final double minDistance;
    private final double maxDistance;
    private final int durationTicks;
    private Entity target;
    private long lastPathTick;

    public EntityDistanceAction(
            String requestId,
            String type,
            int timeout,
            UUID targetId,
            Mode mode,
            double minDistance,
            double maxDistance,
            int durationTicks
    ) {
        super(requestId, type, Math.max(1, Math.min(timeout, durationTicks)));
        this.targetId = targetId;
        this.mode = mode;
        this.minDistance = Math.max(.5, minDistance);
        this.maxDistance = Math.max(this.minDistance, maxDistance);
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
        }
    }

    @Override
    protected void onTick(ActionContext context) {
        if (target == null || !target.isAlive()) {
            fail("TARGET_GONE");
            onStop(context);
            return;
        }
        if (!context.maid().hasLineOfSight(target)) {
            fail("TARGET_NOT_VISIBLE");
            onStop(context);
            return;
        }
        double distance = context.maid().distanceTo(target);
        data.addProperty("distance", distance);
        if (mode == Mode.APPROACH && distance <= maxDistance) {
            finish(context, "TARGET_DISTANCE_REACHED");
            return;
        }
        if (mode == Mode.AWAY && distance >= minDistance) {
            finish(context, "TARGET_DISTANCE_REACHED");
            return;
        }
        if (mode == Mode.MAINTAIN && context.gameTick() - startTick >= durationTicks) {
            finish(context, "DISTANCE_MAINTAINED");
            return;
        }
        if (mode == Mode.MAINTAIN && distance >= minDistance && distance <= maxDistance) {
            context.motion().halt(id, context.maid());
            context.maid().getLookControl().setLookAt(target, 30, 30);
            return;
        }
        if (context.gameTick() - lastPathTick < 10) return;
        lastPathTick = context.gameTick();
        if (mode == Mode.APPROACH || (mode == Mode.MAINTAIN && distance > maxDistance)) {
            if (!context.motion().moveTo(id, context.maid(), target, .9)) {
                fail("PATH_NOT_FOUND");
                onStop(context);
            }
            return;
        }
        Vec3 away = context.maid().position().subtract(target.position());
        if (away.horizontalDistanceSqr() < .001) away = new Vec3(1, 0, 0);
        away = away.normalize();
        double desired = mode == Mode.AWAY ? minDistance : Math.max(1, minDistance - distance + 1);
        Vec3 destination = context.maid().position().add(away.scale(desired));
        if (!context.motion().moveTo(id, context.maid(), destination.x, destination.y, destination.z, .95)) {
            fail("PATH_NOT_FOUND");
            onStop(context);
        }
    }

    private void finish(ActionContext context, String result) {
        context.motion().release(id, context.maid());
        succeed(result);
    }

    @Override
    protected void onStop(ActionContext context) {
        context.motion().release(id, context.maid());
    }
}
