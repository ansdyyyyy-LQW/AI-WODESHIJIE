package com.maidaibridge.action.impl;

import com.maidaibridge.action.AbstractMaidAction;
import com.maidaibridge.action.ActionContext;
import com.maidaibridge.action.MaidActionSafety;
import com.maidaibridge.controller.MotionPriority;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.world.item.BlockItem;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;

import java.util.ArrayList;
import java.util.List;

/** Places a bounded cuboid with actual EntityMaid inventory and placement APIs. */
public final class PlaceRegionAction extends AbstractMaidAction {
    private final List<BlockPos> targets;
    private final Item item;
    private final BlockItem blockItem;
    private int index;
    private int placed;
    private long lastPathTick;

    public PlaceRegionAction(String requestId, int timeout, BlockPos first, BlockPos second, Item item) {
        super(requestId, "place_region", timeout);
        if (!(item instanceof BlockItem value)) throw new IllegalArgumentException("item is not a block item");
        this.targets = region(first, second);
        this.item = item;
        this.blockItem = value;
    }

    private static List<BlockPos> region(BlockPos first, BlockPos second) {
        long volume = (long) (Math.abs(first.getX() - second.getX()) + 1)
                * (Math.abs(first.getY() - second.getY()) + 1)
                * (Math.abs(first.getZ() - second.getZ()) + 1);
        if (volume > 4096) throw new IllegalArgumentException("region exceeds 4096 blocks");
        List<BlockPos> result = new ArrayList<>();
        for (BlockPos position : BlockPos.betweenClosed(first, second)) result.add(position.immutable());
        return result;
    }

    @Override
    protected void onStart(ActionContext context) {
        MaidActionSafety.requireServerThread(context);
        if (context.motion().acquire(id, MotionPriority.TASK, context.gameTick()).isEmpty()) fail("MOTION_BUSY");
    }

    @Override
    protected void onTick(ActionContext context) {
        while (index < targets.size() && context.level().getBlockState(targets.get(index)).is(blockItem.getBlock())) index++;
        if (index >= targets.size()) {
            data.addProperty("blocks_placed", placed);
            worldDelta.addProperty("blocks_placed", placed);
            succeed("REGION_PLACED");
            context.motion().release(id, context.maid());
            return;
        }
        BlockPos target = targets.get(index);
        if (!MaidActionSafety.loaded(context, target)) {
            fail("CHUNK_NOT_LOADED");
            return;
        }
        if (!context.level().getBlockState(target).canBeReplaced()) {
            fail("TARGET_OCCUPIED");
            return;
        }
        if (context.inventory().count(context.maid(), item) < 1) {
            data.addProperty("remaining_blocks", targets.size() - index);
            fail("NO_MATERIAL");
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
        boolean changed = context.maid().placeItemBlock(target, Direction.UP, stack);
        if (!stack.isEmpty()) context.inventory().insert(context.maid(), stack);
        if (changed && context.level().getBlockState(target).is(blockItem.getBlock())) {
            placed++;
            index++;
        } else {
            fail("PLACE_FAILED");
        }
    }

    @Override
    protected void onStop(ActionContext context) {
        context.motion().release(id, context.maid());
    }
}
