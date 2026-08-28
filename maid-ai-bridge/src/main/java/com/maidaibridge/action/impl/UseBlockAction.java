package com.maidaibridge.action.impl;

import com.maidaibridge.action.AbstractMaidAction;
import com.maidaibridge.action.ActionContext;
import com.maidaibridge.action.MaidActionSafety;
import com.maidaibridge.controller.MotionPriority;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.Container;
import net.minecraft.world.level.block.DoorBlock;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.properties.BlockStateProperties;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.Vec3;

/** Uses supported block APIs; it never calls Level#setBlock to imitate an interaction. */
public final class UseBlockAction extends AbstractMaidAction {
    private final BlockPos target;
    private final InteractionHand hand;
    private final Direction face;
    private long lastPath;

    public UseBlockAction(String requestId, int timeout, BlockPos target) {
        this(requestId, "use_block", timeout, target, InteractionHand.MAIN_HAND, Direction.UP);
    }

    public UseBlockAction(
            String requestId, String type, int timeout, BlockPos target,
            InteractionHand hand, Direction face
    ) {
        super(requestId, type, timeout);
        this.target = target;
        this.hand = hand;
        this.face = face;
    }

    @Override
    protected void onStart(ActionContext context) {
        MaidActionSafety.requireServerThread(context);
        if (!MaidActionSafety.loaded(context, target)) {
            fail("CHUNK_NOT_LOADED");
            return;
        }
        if (context.level().getBlockState(target).isAir()) {
            fail("TARGET_GONE");
            return;
        }
        if (context.motion().acquire(id, MotionPriority.TASK, context.gameTick()).isEmpty()) {
            fail("MOTION_BUSY");
        }
    }

    @Override
    protected void onTick(ActionContext context) {
        if (!MaidActionSafety.within(context, target, 3.5)) {
            if (context.gameTick() - lastPath >= 20) {
                context.motion().moveTo(id, context.maid(), target.getX() + .5, target.getY(), target.getZ() + .5, .8);
                lastPath = context.gameTick();
            }
            return;
        }
        context.motion().release(id, context.maid());
        String safety = MaidActionSafety.validateCloseInteraction(context, target, 3.5);
        if (!safety.isEmpty()) {
            fail(safety);
            return;
        }
        BlockState state = context.level().getBlockState(target);
        try {
            BlockHitResult hit = new BlockHitResult(
                    Vec3.atCenterOf(target).add(Vec3.atLowerCornerOf(face.getNormal()).scale(.5)),
                    face, target, false
            );
            InteractionResult result = state.use(context.level(), null, hand, hit);
            BlockState after = context.level().getBlockState(target);
            data.addProperty("hand", hand.name());
            data.addProperty("face", face.name());
            data.addProperty("interaction_result", result.name());
            if (result.consumesAction()) {
                context.maid().swing(hand);
                if (!after.equals(state)) worldDelta.addProperty("block_interacted", target.asLong());
                succeed("BLOCK_INTERACTED");
                return;
            }
        } catch (RuntimeException playerOnly) {
            data.addProperty("player_context_required", true);
        }
        // Mature production chains keep owning containers/workstations. The legacy
        // use_block probe may report readiness; generic interact_block never fakes it.
        if (type.equals("use_block") && context.level().getBlockEntity(target) instanceof Container) {
            data.addProperty("container_ready", true);
            succeed("CONTAINER_READY");
            return;
        }
        if (state.getBlock() instanceof DoorBlock door && state.hasProperty(BlockStateProperties.OPEN)) {
            boolean before = state.getValue(BlockStateProperties.OPEN);
            door.setOpen(context.maid(), context.level(), state, target, !before);
            BlockState after = context.level().getBlockState(target);
            if (after.hasProperty(BlockStateProperties.OPEN)
                    && after.getValue(BlockStateProperties.OPEN) != before) {
                data.addProperty("open", after.getValue(BlockStateProperties.OPEN));
                worldDelta.addProperty("block_interacted", target.asLong());
                succeed("USED");
            } else {
                fail("INTERACTION_REJECTED");
            }
            return;
        }
        fail(data.has("player_context_required")
                ? "PLAYER_CONTEXT_REQUIRED" : "UNSUPPORTED_BLOCK_INTERACTION");
    }

    @Override
    protected void onStop(ActionContext context) {
        context.motion().release(id, context.maid());
    }
}
