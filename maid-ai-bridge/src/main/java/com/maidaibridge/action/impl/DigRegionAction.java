package com.maidaibridge.action.impl;

import com.maidaibridge.action.AbstractMaidAction;
import com.maidaibridge.action.ActionContext;
import com.maidaibridge.action.MaidActionSafety;
import com.maidaibridge.controller.MotionPriority;
import net.minecraft.core.BlockPos;
import net.minecraft.world.level.block.state.BlockState;

import java.util.ArrayList;
import java.util.List;

/** Breaks a bounded region through normal EntityMaid reach/visibility rules. */
public final class DigRegionAction extends AbstractMaidAction {
    private final List<BlockPos> targets;
    private int index;
    private int workTicks = -1;
    private int broken;
    private long lastPathTick;

    public DigRegionAction(String requestId, int timeout, BlockPos first, BlockPos second) {
        super(requestId, "dig_region", timeout);
        this.targets = region(first, second);
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
        while (index < targets.size() && context.level().getBlockState(targets.get(index)).isAir()) index++;
        if (index >= targets.size()) {
            data.addProperty("blocks_broken", broken);
            worldDelta.addProperty("blocks_broken", broken);
            succeed("REGION_DUG");
            context.motion().release(id, context.maid());
            return;
        }
        BlockPos target = targets.get(index);
        if (!MaidActionSafety.loaded(context, target)) {
            fail("CHUNK_NOT_LOADED");
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
        if (!context.maid().canDestroyBlock(target)) {
            fail("BLOCK_PROTECTED");
            return;
        }
        String toolFailure = MaidActionSafety.validateHarvestTool(
                context, context.level().getBlockState(target)
        );
        if (!toolFailure.isEmpty()) {
            fail(toolFailure);
            return;
        }
        if (!context.visibility().canSee(context.maid(), context.level(), target)) {
            fail("TARGET_NOT_VISIBLE");
            return;
        }
        if (workTicks < 0) {
            BlockState state = context.level().getBlockState(target);
            float hardness = state.getDestroySpeed(context.level(), target);
            if (hardness < 0) {
                fail("BLOCK_PROTECTED");
                return;
            }
            workTicks = Math.max(4, Math.min(160, (int) Math.ceil(hardness * 15)));
        }
        if (--workTicks <= 0) {
            boolean changed = context.maid().destroyBlock(target, true);
            if (changed && context.level().getBlockState(target).isAir()) {
                broken++;
                index++;
                workTicks = -1;
            } else {
                fail("BREAK_FAILED");
            }
        }
    }

    @Override
    protected void onStop(ActionContext context) {
        context.motion().release(id, context.maid());
    }
}
