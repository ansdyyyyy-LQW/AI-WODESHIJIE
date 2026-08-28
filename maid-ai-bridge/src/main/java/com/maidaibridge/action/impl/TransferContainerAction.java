package com.maidaibridge.action.impl;

import com.maidaibridge.action.AbstractMaidAction;
import com.maidaibridge.action.ActionContext;
import com.maidaibridge.action.MaidActionSafety;
import com.maidaibridge.controller.MotionPriority;
import net.minecraft.core.BlockPos;
import net.minecraft.world.Container;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;

public final class TransferContainerAction extends AbstractMaidAction {
    private final BlockPos target;
    private final Item item;
    private final int requested;
    private final boolean toContainer;
    private final boolean allowPartial;
    private long lastPath;

    public TransferContainerAction(
            String requestId, int timeout, BlockPos target, Item item, int requested,
            boolean toContainer, boolean allowPartial
    ) {
        this(requestId, "transfer_container", timeout, target, item, requested, toContainer, allowPartial);
    }

    public TransferContainerAction(
            String requestId, String type, int timeout, BlockPos target, Item item, int requested,
            boolean toContainer, boolean allowPartial
    ) {
        super(requestId, type, timeout);
        this.target = target;
        this.item = item;
        this.requested = Math.max(1, requested);
        this.toContainer = toContainer;
        this.allowPartial = allowPartial;
    }

    @Override
    protected void onStart(ActionContext context) {
        MaidActionSafety.requireServerThread(context);
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
            return;
        }
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

        int moved = toContainer ? moveToContainer(context, container) : moveFromContainer(context, container);
        data.addProperty("requested", requested);
        data.addProperty("moved", moved);
        data.addProperty("remaining", Math.max(0, requested - moved));
        data.addProperty("partial", moved > 0 && moved < requested);
        if (moved == requested || (allowPartial && moved > 0)) {
            container.setChanged();
            worldDelta.addProperty("container_changed", target.asLong());
            succeed(moved == requested ? "TRANSFERRED" : "PARTIAL_TRANSFER");
        } else if (moved == 0) {
            fail(toContainer ? "NO_MATERIAL_OR_CAPACITY" : "NO_MATERIAL_OR_INVENTORY_SPACE");
        } else {
            // Partial mutation is disallowed when allow_partial=false. The helper methods
            // pre-compute capacity/availability, so this branch should never be reached.
            fail("TRANSFER_INCOMPLETE");
        }
    }

    private int moveToContainer(ActionContext context, Container container) {
        int available = context.inventory().available(context.maid(), item);
        ItemStack sample = new ItemStack(item, Math.min(requested, Math.max(1, available)));
        int capacity = containerCapacity(container, sample);
        int movable = Math.min(requested, Math.min(available, capacity));
        if (movable <= 0 || (!allowPartial && movable < requested)) return 0;
        ItemStack moving = context.inventory().extract(context.maid(), item, movable, false);
        int original = moving.getCount();
        for (int slot = 0; slot < container.getContainerSize() && !moving.isEmpty(); slot++) {
            if (!container.canPlaceItem(slot, moving)) continue;
            ItemStack current = container.getItem(slot);
            if (current.isEmpty()) {
                int amount = Math.min(moving.getCount(), Math.min(moving.getMaxStackSize(), container.getMaxStackSize()));
                container.setItem(slot, moving.copyWithCount(amount));
                moving.shrink(amount);
            } else if (ItemStack.isSameItemSameTags(current, moving)) {
                int max = Math.min(current.getMaxStackSize(), container.getMaxStackSize());
                int amount = Math.min(moving.getCount(), Math.max(0, max - current.getCount()));
                if (amount > 0) {
                    current.grow(amount);
                    moving.shrink(amount);
                    container.setItem(slot, current);
                }
            }
        }
        if (!moving.isEmpty()) context.inventory().insert(context.maid(), moving);
        return original - moving.getCount();
    }

    private int moveFromContainer(ActionContext context, Container container) {
        int available = 0;
        for (int slot = 0; slot < container.getContainerSize(); slot++) {
            ItemStack stack = container.getItem(slot);
            if (stack.is(item)) available += stack.getCount();
        }
        ItemStack sample = new ItemStack(item, Math.min(requested, Math.max(1, available)));
        int capacity = context.inventory().capacity(context.maid(), sample);
        int movable = Math.min(requested, Math.min(available, capacity));
        if (movable <= 0 || (!allowPartial && movable < requested)) return 0;
        int remaining = movable;
        int moved = 0;
        for (int slot = 0; slot < container.getContainerSize() && remaining > 0; slot++) {
            ItemStack current = container.getItem(slot);
            if (!current.is(item)) continue;
            int take = Math.min(remaining, current.getCount());
            ItemStack part = current.copyWithCount(take);
            if (!context.inventory().insert(context.maid(), part)) break;
            current.shrink(take);
            container.setItem(slot, current);
            remaining -= take;
            moved += take;
        }
        return moved;
    }

    private static int containerCapacity(Container container, ItemStack stack) {
        int capacity = 0;
        for (int slot = 0; slot < container.getContainerSize(); slot++) {
            if (!container.canPlaceItem(slot, stack)) continue;
            ItemStack current = container.getItem(slot);
            if (current.isEmpty()) {
                capacity += Math.min(stack.getMaxStackSize(), container.getMaxStackSize());
            } else if (ItemStack.isSameItemSameTags(current, stack)) {
                capacity += Math.max(0, Math.min(current.getMaxStackSize(), container.getMaxStackSize()) - current.getCount());
            }
        }
        return capacity;
    }

    @Override
    protected void onStop(ActionContext context) {
        context.motion().release(id, context.maid());
    }
}
