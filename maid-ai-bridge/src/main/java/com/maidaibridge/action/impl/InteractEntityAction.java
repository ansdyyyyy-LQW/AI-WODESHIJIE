package com.maidaibridge.action.impl;

import com.maidaibridge.BridgeConfig;
import com.maidaibridge.action.AbstractMaidAction;
import com.maidaibridge.action.ActionContext;
import com.maidaibridge.controller.MotionPriority;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.item.ItemEntity;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.item.ItemStack;

import java.util.UUID;

/** Unified close entity interaction without substituting a fake player for the maid. */
public final class InteractEntityAction extends AbstractMaidAction {
    private final UUID targetId;
    private final InteractionHand hand;
    private Entity target;
    private long lastPathTick;

    public InteractEntityAction(String requestId, int timeout, UUID targetId, InteractionHand hand) {
        super(requestId, "interact_entity", timeout);
        this.targetId = targetId;
        this.hand = hand;
    }

    @Override
    protected void onStart(ActionContext context) {
        target = context.level().getEntity(targetId);
        int radius = BridgeConfig.ENTITY_OBSERVE_RADIUS.get();
        if (target == null) {
            fail("TARGET_STATE_UNKNOWN");
            context.motion().release(id, context.maid());
            return;
        }
        if (target == context.maid() || target.level() != context.level() || target.isRemoved() || target.isSpectator()) {
            fail("INVALID_TARGET");
            context.motion().release(id, context.maid());
            return;
        }
        if (!target.isAlive()) {
            fail("TARGET_DEAD");
            context.motion().release(id, context.maid());
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
        Entity current = context.level().getEntity(targetId);
        if (current == null) {
            fail("TARGET_STATE_UNKNOWN");
            context.motion().release(id, context.maid());
            return;
        }
        target = current;
        if (target.level() != context.level()) {
            fail("WRONG_DIMENSION");
            context.motion().release(id, context.maid());
            return;
        }
        if (target.isRemoved()) {
            fail("TARGET_GONE");
            context.motion().release(id, context.maid());
            return;
        }
        if (!target.isAlive()) {
            fail("TARGET_DEAD");
            context.motion().release(id, context.maid());
            return;
        }
        if (target == context.maid() || target.isSpectator()) {
            fail("INVALID_TARGET");
            context.motion().release(id, context.maid());
            return;
        }
        if (context.maid().distanceToSqr(target) > 9) {
            if (context.gameTick() - lastPathTick >= 20) {
                context.motion().moveTo(id, context.maid(), target, .8);
                lastPathTick = context.gameTick();
            }
            return;
        }
        context.motion().release(id, context.maid());
        context.maid().getLookControl().setLookAt(target, 30, 30);
        String finalFailure = finalExecutionFailure(context);
        if (finalFailure != null) {
            fail(finalFailure);
            context.motion().release(id, context.maid());
            return;
        }
        context.maid().swing(hand);
        if (target instanceof ItemEntity itemEntity) {
            ItemStack before = itemEntity.getItem().copy();
            if (!context.inventory().hasSpace(context.maid(), before)) {
                fail("INVENTORY_FULL");
                context.motion().release(id, context.maid());
                return;
            }
            boolean accepted = context.maid().pickupItem(itemEntity, true);
            int remaining = itemEntity.isAlive() ? itemEntity.getItem().getCount() : 0;
            int pickedUp = Math.max(0, before.getCount() - remaining);
            if (accepted && pickedUp > 0) {
                data.addProperty("picked_up", pickedUp);
                worldDelta.addProperty("entity_interacted", target.getUUID().toString());
                succeed("ENTITY_INTERACTED");
            } else fail("ITEM_PICKUP_REJECTED");
            return;
        }
        try {
            InteractionResult result = target.interact(null, hand);
            data.addProperty("interaction_result", result.name());
            if (result.consumesAction()) {
                worldDelta.addProperty("entity_interacted", target.getUUID().toString());
                succeed("ENTITY_INTERACTED");
                return;
            }
        } catch (RuntimeException ignored) {
            data.addProperty("player_context_required", true);
        }
        ItemStack held = context.maid().getItemInHand(hand);
        if (!held.isEmpty() && target instanceof LivingEntity living) {
            try {
                InteractionResult result = held.interactLivingEntity(null, living, hand);
                context.maid().setItemInHand(hand, held);
                data.addProperty("interaction_result", result.name());
                if (result.consumesAction()) {
                    worldDelta.addProperty("entity_interacted", target.getUUID().toString());
                    succeed("ENTITY_INTERACTED");
                    return;
                }
            } catch (RuntimeException ignored) {
                // The item requires a real Player-only path, which EntityMaid cannot use.
            }
        }
        // Replacing EntityMaid with a fake player is forbidden, so Player-only
        // targets fail explicitly rather than pretending an interaction happened.
        fail(data.has("player_context_required")
                ? "PLAYER_CONTEXT_REQUIRED" : "UNSUPPORTED_ENTITY_INTERACTION");
    }

    @Override
    protected void onStop(ActionContext context) {
        context.motion().release(id, context.maid());
    }

    /** Re-resolves and validates the target on the exact Tick that invokes interact. */
    private String finalExecutionFailure(ActionContext context) {
        Entity current = context.level().getEntity(targetId);
        if (current == null) return "TARGET_STATE_UNKNOWN";
        if (current.level() != context.level()) return "WRONG_DIMENSION";
        if (current.isRemoved()) return "TARGET_GONE";
        if (!current.isAlive()) return "TARGET_DEAD";
        if (current == context.maid() || current.isSpectator()) return "INVALID_TARGET";
        int observeRadius = BridgeConfig.ENTITY_OBSERVE_RADIUS.get();
        double distance = context.maid().distanceToSqr(current);
        if (distance > observeRadius * observeRadius) return "TARGET_STATE_UNKNOWN";
        if (distance > 9) return "TARGET_OUT_OF_RANGE";
        if (!context.maid().hasLineOfSight(current)) return "TARGET_NOT_VISIBLE";
        target = current;
        return null;
    }
}
