package com.maidaibridge.action.impl;

import com.maidaibridge.action.AbstractMaidAction;
import com.maidaibridge.action.ActionContext;
import com.maidaibridge.action.MaidActionSafety;
import com.maidaibridge.controller.MotionPriority;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.item.BlockItem;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.context.UseOnContext;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.Vec3;

/** Generic held-item-on-face entry point, using EntityMaid's real placement API where supported. */
public final class UseItemOnBlockAction extends AbstractMaidAction {
    private final BlockPos clicked;
    private final Direction face;
    private final InteractionHand hand;
    private long lastPathTick;

    public UseItemOnBlockAction(
            String requestId, int timeout, BlockPos clicked, Direction face, InteractionHand hand
    ) {
        super(requestId, "use_item_on_block", timeout);
        this.clicked = clicked;
        this.face = face;
        this.hand = hand;
    }

    @Override
    protected void onStart(ActionContext context) {
        if (!MaidActionSafety.loaded(context, clicked)) {
            fail("CHUNK_NOT_LOADED");
            return;
        }
        if (!MaidActionSafety.visible(context, clicked)) {
            fail("TARGET_NOT_VISIBLE");
            return;
        }
        if (context.maid().getItemInHand(hand).isEmpty()) {
            fail("EMPTY_HAND");
            return;
        }
        if (context.motion().acquire(id, MotionPriority.TASK, context.gameTick()).isEmpty()) {
            fail("MOTION_BUSY");
        }
    }

    @Override
    protected void onTick(ActionContext context) {
        if (!MaidActionSafety.within(context, clicked, 3.5)) {
            if (context.gameTick() - lastPathTick >= 20) {
                context.motion().moveTo(id, context.maid(), clicked.getX() + .5, clicked.getY(), clicked.getZ() + .5, .8);
                lastPathTick = context.gameTick();
            }
            return;
        }
        context.motion().halt(id, context.maid());
        String safety = MaidActionSafety.validateCloseInteraction(context, clicked, 3.5);
        if (!safety.isEmpty()) {
            fail(safety);
            context.motion().release(id, context.maid());
            return;
        }
        ItemStack stack = context.maid().getItemInHand(hand);
        if (stack.getItem() instanceof BlockItem blockItem) {
            BlockPos placement = clicked.relative(face);
            if (!context.level().getBlockState(placement).canBeReplaced()) {
                fail("TARGET_OCCUPIED");
                context.motion().release(id, context.maid());
                return;
            }
            if (!context.maid().canPlaceBlock(placement)) {
                fail("BLOCK_PROTECTED");
                context.motion().release(id, context.maid());
                return;
            }
            boolean placed = context.maid().placeItemBlock(hand, placement, face, stack);
            context.maid().setItemInHand(hand, stack);
            if (placed && context.level().getBlockState(placement).is(blockItem.getBlock())) {
                worldDelta.addProperty("block_placed", placement.asLong());
                data.addProperty("hand", hand.name());
                succeed("ITEM_USED_ON_BLOCK");
            } else fail("ITEM_USE_FAILED");
            context.motion().release(id, context.maid());
            return;
        }
        // UseOnContext explicitly supports a null Player. This lets compatible
        // vanilla/Forge items run their real logic without substituting a fake body.
        try {
            BlockHitResult hit = new BlockHitResult(
                    Vec3.atCenterOf(clicked).add(Vec3.atLowerCornerOf(face.getNormal()).scale(.5)),
                    face, clicked, false
            );
            UseOnContext useContext = new UseOnContext(context.level(), null, hand, stack, hit);
            InteractionResult result = stack.onItemUseFirst(useContext);
            if (!result.consumesAction()) result = stack.useOn(useContext);
            context.maid().setItemInHand(hand, stack);
            data.addProperty("hand", hand.name());
            data.addProperty("interaction_result", result.name());
            if (result.consumesAction()) {
                context.maid().swing(hand);
                succeed("ITEM_USED_ON_BLOCK");
            } else fail("ITEM_USE_FAILED");
        } catch (RuntimeException unsupported) {
            fail("UNSUPPORTED_ITEM_ON_BLOCK");
        }
        context.motion().release(id, context.maid());
    }

    @Override
    protected void onStop(ActionContext context) {
        context.motion().release(id, context.maid());
    }
}
