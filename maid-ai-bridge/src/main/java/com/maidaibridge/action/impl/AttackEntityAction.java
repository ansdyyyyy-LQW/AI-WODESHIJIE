package com.maidaibridge.action.impl;

import com.maidaibridge.BridgeConfig;
import com.maidaibridge.action.AbstractMaidAction;
import com.maidaibridge.action.ActionContext;
import com.maidaibridge.controller.MotionPriority;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.LivingEntity;

import java.util.UUID;

/** Bounded attack loop. Attack timing and stopping are handled entirely in Bridge. */
public final class AttackEntityAction extends AbstractMaidAction {
    private final UUID targetId;
    private final int maxAttacks;
    private final int maxDurationTicks;
    private final String stopCondition;
    private LivingEntity target;
    private long lastAttack;
    private int attacks;

    public AttackEntityAction(
            String requestId,
            int timeout,
            UUID targetId,
            int maxAttacks,
            int maxDurationTicks,
            String stopCondition
    ) {
        super(requestId, "attack_entity", Math.max(1, Math.min(timeout, maxDurationTicks)));
        this.targetId = targetId;
        this.maxAttacks = Math.max(1, Math.min(64, maxAttacks));
        this.maxDurationTicks = Math.max(1, maxDurationTicks);
        this.stopCondition = stopCondition == null ? "ANY" : stopCondition;
    }

    @Override
    protected void onStart(ActionContext context) {
        Entity entity = context.level().getEntity(targetId);
        if (!(entity instanceof LivingEntity living) || !living.isAlive()) {
            fail("TARGET_GONE");
            return;
        }
        int observeRadius = BridgeConfig.ENTITY_OBSERVE_RADIUS.get();
        if (context.maid().distanceToSqr(living) > observeRadius * observeRadius
                || !context.maid().hasLineOfSight(living)) {
            fail("TARGET_NOT_VISIBLE");
            return;
        }
        target = living;
        if (context.motion().acquire(id, MotionPriority.COMBAT, context.gameTick()).isEmpty()) {
            fail("MOTION_BUSY");
        }
    }

    @Override
    protected void onTick(ActionContext context) {
        if (target == null || !target.isAlive()) {
            finish(context, "TARGET_GONE");
            return;
        }
        if (attacks >= maxAttacks) {
            finish(context, "MAX_ATTACKS_REACHED");
            return;
        }
        if (context.gameTick() - startTick >= maxDurationTicks) {
            finish(context, "MAX_DURATION_REACHED");
            return;
        }
        double distanceSquared = context.maid().distanceToSqr(target);
        if (distanceSquared > 6.25) {
            if (("OUT_OF_RANGE".equalsIgnoreCase(stopCondition) || "ANY".equalsIgnoreCase(stopCondition))
                    && distanceSquared > 64) {
                finish(context, "OUT_OF_RANGE");
                return;
            }
            context.motion().moveTo(id, context.maid(), target, 1.0);
            return;
        }
        context.motion().halt(id, context.maid());
        context.maid().getLookControl().setLookAt(target, 30, 30);
        if (context.gameTick() - lastAttack >= 10) {
            context.maid().doHurtTarget(target);
            context.maid().swing(InteractionHand.MAIN_HAND);
            attacks++;
            lastAttack = context.gameTick();
            data.addProperty("attack_count", attacks);
        }
    }

    private void finish(ActionContext context, String code) {
        data.addProperty("attack_count", attacks);
        context.motion().release(id, context.maid());
        succeed(code);
    }

    @Override
    protected void onStop(ActionContext context) {
        context.motion().release(id, context.maid());
    }
}
