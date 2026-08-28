package com.maidaibridge.action.impl;

import com.maidaibridge.action.AbstractMaidAction;
import com.maidaibridge.action.ActionContext;
import com.maidaibridge.action.MaidActionSafety;
import com.maidaibridge.controller.MotionPriority;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.level.block.state.BlockState;

public final class BreakBlockAction extends AbstractMaidAction {
    private final BlockPos target;
    private int workTicks = -1;
    private long lastPathTick;
    private String originalBlockId = "";

    public BreakBlockAction(String requestId, int timeout, BlockPos target) {
        super(requestId, "break_block", timeout);
        this.target = target;
    }

    @Override
    protected void onStart(ActionContext context) {
        MaidActionSafety.requireServerThread(context);
        if (!MaidActionSafety.loaded(context, target)) {
            fail("CHUNK_NOT_LOADED");
            return;
        }
        BlockState state = context.level().getBlockState(target);
        if (state.isAir()) {
            succeed("ALREADY_AIR");
            return;
        }
        originalBlockId = BuiltInRegistries.BLOCK.getKey(state.getBlock()).toString();
        if (!context.maid().canDestroyBlock(target)) {
            fail("BLOCK_PROTECTED");
            return;
        }
        String toolFailure = MaidActionSafety.validateHarvestTool(context, state);
        if (!toolFailure.isEmpty()) {
            fail(toolFailure);
            return;
        }
        if (context.motion().acquire(id, MotionPriority.TASK, context.gameTick()).isEmpty()) {
            fail("MOTION_BUSY");
        }
    }

    @Override
    protected void onTick(ActionContext context) {
        if (context.level().getBlockState(target).isAir()) {
            succeed("BROKEN");
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
        if (!MaidActionSafety.visible(context, target)) {
            fail("TARGET_NOT_VISIBLE");
            return;
        }
        if (workTicks < 0) {
            BlockState state = context.level().getBlockState(target);
            String toolFailure = MaidActionSafety.validateHarvestTool(context, state);
            if (!toolFailure.isEmpty()) {
                fail(toolFailure);
                return;
            }
            float hardness = state.getDestroySpeed(context.level(), target);
            if (hardness < 0) {
                fail("BLOCK_PROTECTED");
                return;
            }
            workTicks = Math.max(5, Math.min(200, (int) Math.ceil(hardness * 20)));
        }
        if (--workTicks <= 0) {
            String toolFailure = MaidActionSafety.validateHarvestTool(
                    context, context.level().getBlockState(target)
            );
            if (!toolFailure.isEmpty()) {
                fail(toolFailure);
                return;
            }
            boolean ok = context.maid().destroyBlock(target, true);
            if (ok && context.level().getBlockState(target).isAir()) {
                com.google.gson.JsonObject position = new com.google.gson.JsonObject();
                position.addProperty("x", target.getX());
                position.addProperty("y", target.getY());
                position.addProperty("z", target.getZ());
                data.add("position", position);
                data.addProperty("block_id", originalBlockId);
                worldDelta.addProperty("block_broken", true);
                worldDelta.addProperty("position_long", target.asLong());
                worldDelta.addProperty("block_id", originalBlockId);
                succeed("BROKEN");
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
