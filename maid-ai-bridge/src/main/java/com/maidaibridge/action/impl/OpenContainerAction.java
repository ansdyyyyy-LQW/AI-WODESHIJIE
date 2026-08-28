package com.maidaibridge.action.impl;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.maidaibridge.action.AbstractMaidAction;
import com.maidaibridge.action.ActionContext;
import com.maidaibridge.action.MaidActionSafety;
import com.maidaibridge.controller.MotionPriority;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.Container;

/** Establishes and verifies a real, close container context for the maid. */
public final class OpenContainerAction extends AbstractMaidAction {
    private final BlockPos target;
    private long lastPathTick;

    public OpenContainerAction(String requestId, int timeout, BlockPos target) {
        super(requestId, "open_container", timeout);
        this.target = target;
    }

    @Override
    protected void onStart(ActionContext context) {
        if (!MaidActionSafety.loaded(context, target)) {
            fail("CHUNK_NOT_LOADED");
            return;
        }
        if (!(context.level().getBlockEntity(target) instanceof Container)) {
            fail("NO_CONTAINER");
            return;
        }
        if (context.motion().acquire(id, MotionPriority.TASK, context.gameTick()).isEmpty()) {
            fail("MOTION_BUSY");
        }
    }

    @Override
    protected void onTick(ActionContext context) {
        if (!(context.level().getBlockEntity(target) instanceof Container container)) {
            fail("NO_CONTAINER");
            context.motion().release(id, context.maid());
            return;
        }
        if (!MaidActionSafety.within(context, target, 3.5)) {
            if (context.gameTick() - lastPathTick >= 20) {
                context.motion().moveTo(id, context.maid(), target.getX() + .5, target.getY(), target.getZ() + .5, .8);
                lastPathTick = context.gameTick();
            }
            return;
        }
        context.motion().release(id, context.maid());
        String safety = MaidActionSafety.validateCloseInteraction(context, target, 3.5);
        if (!safety.isEmpty()) {
            fail(safety);
            return;
        }
        JsonArray slots = new JsonArray();
        for (int index = 0; index < container.getContainerSize(); index++) {
            var stack = container.getItem(index);
            if (stack.isEmpty()) continue;
            JsonObject row = new JsonObject();
            row.addProperty("slot", index);
            row.addProperty("id", BuiltInRegistries.ITEM.getKey(stack.getItem()).toString());
            row.addProperty("count", stack.getCount());
            slots.add(row);
        }
        data.add("slots", slots);
        data.addProperty("container_open", true);
        succeed("CONTAINER_READY");
    }

    @Override
    protected void onStop(ActionContext context) {
        context.motion().release(id, context.maid());
    }
}
