package com.maidaibridge.action.impl;

import com.maidaibridge.action.AbstractMaidAction;
import com.maidaibridge.action.ActionContext;
import com.maidaibridge.controller.MotionPriority;
import net.minecraft.world.phys.Vec3;

/** One bounded relative move. Direction is fixed from the maid's facing at start. */
public final class RelativeMoveAction extends AbstractMaidAction {
    private final double maxDistance;
    private final String stopCondition;
    private final boolean sprint;
    private final int quarterTurns;
    private Vec3 start;
    private Vec3 target;
    private long lastProgressTick;
    private double lastTravel;

    public RelativeMoveAction(
            String requestId,
            String type,
            int timeout,
            double maxDistance,
            String stopCondition,
            int quarterTurns,
            boolean sprint
    ) {
        super(requestId, type, timeout);
        this.maxDistance = Math.max(.25, Math.min(24, maxDistance));
        this.stopCondition = stopCondition == null ? "ANY" : stopCondition;
        this.quarterTurns = quarterTurns;
        this.sprint = sprint;
    }

    @Override
    protected void onStart(ActionContext context) {
        if (context.motion().acquire(id, MotionPriority.TASK, context.gameTick()).isEmpty()) {
            fail("MOTION_BUSY");
            return;
        }
        start = context.maid().position();
        double radians = Math.toRadians(context.maid().getYRot() + quarterTurns * 90.0);
        double dx = -Math.sin(radians) * maxDistance;
        double dz = Math.cos(radians) * maxDistance;
        target = start.add(dx, 0, dz);
        context.maid().setSprinting(sprint);
        lastProgressTick = context.gameTick();
        requestPath(context);
    }

    private void requestPath(ActionContext context) {
        boolean accepted = context.motion().moveTo(
                id, context.maid(), target.x, target.y, target.z, sprint ? 1.25 : .75
        );
        if (!accepted && acceptsObstacleStop()) {
            finish(context, "STOPPED_BY_OBSTACLE");
        } else if (!accepted) {
            fail("PATH_NOT_FOUND");
            onStop(context);
        }
    }

    @Override
    protected void onTick(ActionContext context) {
        double travelled = horizontalDistance(start, context.maid().position());
        data.addProperty("distance_moved", travelled);
        if (travelled >= maxDistance - .25 || horizontalDistance(target, context.maid().position()) <= .5) {
            finish(context, "DISTANCE_REACHED");
            return;
        }
        if (travelled > lastTravel + .12) {
            lastTravel = travelled;
            lastProgressTick = context.gameTick();
        }
        if (context.gameTick() - lastProgressTick >= 30) {
            if (acceptsObstacleStop()) finish(context, "STOPPED_BY_OBSTACLE");
            else {
                fail("OBSTACLE");
                onStop(context);
            }
        } else if (!context.maid().getNavigation().isInProgress() && context.gameTick() % 10 == 0) {
            requestPath(context);
        }
    }

    private boolean acceptsObstacleStop() {
        return "ANY".equalsIgnoreCase(stopCondition) || "OBSTACLE".equalsIgnoreCase(stopCondition);
    }

    private void finish(ActionContext context, String result) {
        context.maid().setSprinting(false);
        context.motion().release(id, context.maid());
        succeed(result);
    }

    private static double horizontalDistance(Vec3 left, Vec3 right) {
        double dx = left.x - right.x;
        double dz = left.z - right.z;
        return Math.sqrt(dx * dx + dz * dz);
    }

    @Override
    protected void onStop(ActionContext context) {
        context.maid().setSprinting(false);
        context.motion().release(id, context.maid());
    }
}
