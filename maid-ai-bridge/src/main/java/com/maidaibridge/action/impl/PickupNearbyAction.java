package com.maidaibridge.action.impl;

import com.maidaibridge.action.AbstractMaidAction;
import com.maidaibridge.action.ActionContext;
import com.maidaibridge.controller.MotionPriority;
import net.minecraft.world.entity.item.ItemEntity;
import net.minecraft.world.item.Item;

import java.util.Comparator;
import java.util.List;

public final class PickupNearbyAction extends AbstractMaidAction {
    private final double radius;
    private final Item filter;
    private ItemEntity target;

    public PickupNearbyAction(String requestId, int timeout, double radius, Item filter) {
        super(requestId, "pickup_nearby", timeout);
        this.radius = Math.max(1, Math.min(24, radius));
        this.filter = filter;
    }

    @Override
    protected void onStart(ActionContext context) {
        List<ItemEntity> items = context.level().getEntitiesOfClass(
                ItemEntity.class,
                context.maid().getBoundingBox().inflate(radius),
                entity -> entity.isAlive() && (filter == null || entity.getItem().is(filter))
        );
        target = items.stream().min(Comparator.comparingDouble(context.maid()::distanceToSqr)).orElse(null);
        if (target == null) {
            fail("TARGET_GONE");
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
            return;
        }
        if (context.maid().distanceToSqr(target) > 4) {
            context.motion().moveTo(id, context.maid(), target, .9);
            return;
        }
        context.motion().release(id, context.maid());
        int before = target.getItem().getCount();
        boolean ok = context.maid().pickupItem(target, true);
        int after = target.isAlive() ? target.getItem().getCount() : 0;
        int picked = Math.max(0, before - after);
        data.addProperty("picked", picked);
        if (ok && picked > 0) {
            worldDelta.addProperty("items_picked", picked);
            succeed("ITEM_ACQUIRED");
        } else {
            fail("INVENTORY_FULL");
        }
    }

    @Override
    protected void onStop(ActionContext context) {
        context.motion().release(id, context.maid());
    }
}
