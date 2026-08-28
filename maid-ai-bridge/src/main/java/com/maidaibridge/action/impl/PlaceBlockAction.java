package com.maidaibridge.action.impl;

import com.maidaibridge.action.AbstractMaidAction;
import com.maidaibridge.action.ActionContext;
import com.maidaibridge.action.MaidActionSafety;
import com.maidaibridge.controller.MotionPriority;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.item.BlockItem;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;

public final class PlaceBlockAction extends AbstractMaidAction {
    private final BlockPos target;
    private final Item item;
    private final Direction face;
    private long lastPathTick;

    public PlaceBlockAction(String requestId, int timeout, BlockPos target, Item item, Direction face) {
        super(requestId, "place_block", timeout);
        this.target = target;
        this.item = item;
        this.face = face;
    }

    @Override
    protected void onStart(ActionContext context) {
        MaidActionSafety.requireServerThread(context);
        if (!(item instanceof BlockItem blockItem)) {
            fail("ITEM_IS_NOT_BLOCK");
            return;
        }
        if (!MaidActionSafety.loaded(context, target)) {
            fail("CHUNK_NOT_LOADED");
            return;
        }
        if (context.level().getBlockState(target).is(blockItem.getBlock())) {
            data.addProperty("already_present", true);
            recordPlacementResult();
            succeed("ALREADY_PLACED");
            return;
        }
        if (!context.level().getBlockState(target).canBeReplaced()) {
            fail("TARGET_OCCUPIED");
            return;
        }
        if (!context.maid().canPlaceBlock(target)) {
            fail("BLOCK_PROTECTED");
            return;
        }
        if (!MaidActionSafety.visible(context, target)) {
            fail("TARGET_NOT_VISIBLE");
            return;
        }
        if (context.inventory().count(context.maid(), item) < 1) {
            fail("NO_MATERIAL");
            return;
        }
        if (context.motion().acquire(id, MotionPriority.TASK, context.gameTick()).isEmpty()) {
            fail("MOTION_BUSY");
        }
    }

    @Override
    protected void onTick(ActionContext context) {
        if (item instanceof BlockItem blockItem && context.level().getBlockState(target).is(blockItem.getBlock())) {
            data.addProperty("already_present", true);
            recordPlacementResult();
            succeed("ALREADY_PLACED");
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
        context.motion().halt(id, context.maid());
        if (!context.level().getBlockState(target).canBeReplaced()) {
            fail("TARGET_OCCUPIED");
            return;
        }
        if (!context.maid().canPlaceBlock(target)) {
            fail("BLOCK_PROTECTED");
            return;
        }
        if (!MaidActionSafety.visible(context, target)) {
            fail("TARGET_NOT_VISIBLE");
            return;
        }
        ItemStack stack = context.inventory().extract(context.maid(), item, 1, false);
        if (stack.isEmpty()) {
            fail("NO_MATERIAL");
            return;
        }
        boolean ok = context.maid().placeItemBlock(target, face, stack);
        if (!stack.isEmpty()) context.inventory().insert(context.maid(), stack);
        if (ok && item instanceof BlockItem blockItem && context.level().getBlockState(target).is(blockItem.getBlock())) {
            recordPlacementResult();
            succeed("PLACED");
        } else {
            fail("PLACE_FAILED");
        }
    }

    @Override
    protected void onStop(ActionContext context) {
        context.motion().release(id, context.maid());
    }

    private void recordPlacementResult() {
        com.google.gson.JsonObject position = new com.google.gson.JsonObject();
        position.addProperty("x", target.getX());
        position.addProperty("y", target.getY());
        position.addProperty("z", target.getZ());
        data.add("position", position);
        data.addProperty("block_id", BuiltInRegistries.ITEM.getKey(item).toString());
        worldDelta.addProperty("block_placed", true);
        worldDelta.addProperty("position_long", target.asLong());
        worldDelta.addProperty("block_id", BuiltInRegistries.ITEM.getKey(item).toString());
    }
}
