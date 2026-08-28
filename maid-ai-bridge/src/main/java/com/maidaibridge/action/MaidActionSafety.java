package com.maidaibridge.action;

import net.minecraft.core.BlockPos;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.block.state.BlockState;

/** Concrete server-thread, range and visibility checks shared by world actions. */
public final class MaidActionSafety {
    public static void requireServerThread(ActionContext context) {
        if (context.level().getServer() == null || !context.level().getServer().isSameThread()) {
            throw new IllegalStateException("WORLD_THREAD_REQUIRED");
        }
    }

    public static boolean loaded(ActionContext context, BlockPos position) {
        return context.level().hasChunkAt(position);
    }

    public static boolean within(ActionContext context, BlockPos position, double range) {
        return context.maid().distanceToSqr(
                position.getX() + 0.5,
                position.getY() + 0.5,
                position.getZ() + 0.5
        ) <= range * range;
    }

    public static boolean visible(ActionContext context, BlockPos position) {
        return context.visibility().canSee(context.maid(), context.level(), position);
    }

    public static String validateCloseInteraction(ActionContext context, BlockPos position, double range) {
        if (!loaded(context, position)) return "CHUNK_NOT_LOADED";
        if (!within(context, position, range)) return "TOO_FAR";
        if (!visible(context, position)) return "TARGET_NOT_VISIBLE";
        return "";
    }

    public static String validateHarvestTool(ActionContext context, BlockState state) {
        if (!state.requiresCorrectToolForDrops()) return "";
        ItemStack stack = context.maid().getMainHandItem();
        if (stack.isEmpty()) return "NO_TOOL";
        return stack.isCorrectToolForDrops(state) ? "" : "WRONG_TOOL";
    }

    private MaidActionSafety() {}
}
