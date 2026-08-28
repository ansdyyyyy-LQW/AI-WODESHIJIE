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

/** Executes a bounded, pre-planned blueprint segment with exact index reporting. */
public final class BuildChunkAction extends AbstractMaidAction {
    public record Placement(int index, BlockPos position, Item item, Direction face) {}

    private final List<Placement> placements;
    private final boolean allowPartial;
    private final List<Integer> completed = new ArrayList<>();
    private int cursor;
    private long lastPath;

    public BuildChunkAction(
            String requestId, int timeout, List<Placement> placements, boolean allowPartial
    ) {
        super(requestId, "build_chunk", timeout);
        if (placements.isEmpty()) throw new IllegalArgumentException("placements must not be empty");
        if (placements.size() > 128) throw new IllegalArgumentException("build_chunk exceeds 128 placements");
        this.placements = List.copyOf(placements);
        this.allowPartial = allowPartial;
    }

    @Override
    protected void onStart(ActionContext context) {
        MaidActionSafety.requireServerThread(context);
        if (context.motion().acquire(id, MotionPriority.TASK, context.gameTick()).isEmpty()) {
            fail("MOTION_BUSY");
        }
    }

    @Override
    protected void onTick(ActionContext context) {
        while (cursor < placements.size()) {
            Placement current=placements.get(cursor);
            if(!MaidActionSafety.loaded(context,current.position())||!alreadyExpected(context,current))break;
            completed.add(current.index());cursor++;
        }
        if (cursor >= placements.size()) {
            finishData();
            worldDelta.addProperty("blocks_completed", completed.size());
            succeed("BUILD_CHUNK_COMPLETE");
            context.motion().release(id, context.maid());
            return;
        }

        Placement placement = placements.get(cursor);
        BlockPos target = placement.position();
        if (!MaidActionSafety.loaded(context, target)) {
            failAt("CHUNK_NOT_LOADED", placement);
            return;
        }
        if (context.inventory().count(context.maid(), placement.item()) < 1) {
            JsonObjectBuilder.addMissing(data, placement.item());
            failAt("NO_MATERIAL", placement);
            return;
        }
        if (!MaidActionSafety.within(context, target, 3.5)) {
            if (context.gameTick() - lastPath >= 20) {
                context.motion().moveTo(id, context.maid(), target.getX() + .5, target.getY(), target.getZ() + .5, .8);
                lastPath = context.gameTick();
            }
            return;
        }
        context.motion().halt(id, context.maid());
        if (!context.level().getBlockState(target).canBeReplaced()) {
            failAt("TARGET_OCCUPIED", placement);
            return;
        }
        if (!context.maid().canPlaceBlock(target)) {
            failAt("BLOCK_PROTECTED", placement);
            return;
        }
        if (!MaidActionSafety.visible(context, target)) {
            failAt("TARGET_NOT_VISIBLE", placement);
            return;
        }
        ItemStack stack = context.inventory().extract(context.maid(), placement.item(), 1, false);
        if (stack.isEmpty()) {
            JsonObjectBuilder.addMissing(data, placement.item());
            failAt("NO_MATERIAL", placement);
            return;
        }
        boolean placed = context.maid().placeItemBlock(target, placement.face(), stack);
        if (!stack.isEmpty()) context.inventory().insert(context.maid(), stack);
        if (!placed || !alreadyExpected(context, placement)) {
            failAt("PLACE_FAILED", placement);
            return;
        }
        completed.add(placement.index());
        cursor++;
    }

    private boolean alreadyExpected(ActionContext context, Placement placement) {
        if (!(placement.item() instanceof BlockItem blockItem)) return false;
        return context.level().getBlockState(placement.position()).is(blockItem.getBlock());
    }

    private void failAt(String code, Placement placement) {
        finishData();
        data.addProperty("failed_index", placement.index());
        data.addProperty("failed_x", placement.position().getX());
        data.addProperty("failed_y", placement.position().getY());
        data.addProperty("failed_z", placement.position().getZ());
        data.addProperty("partial", !completed.isEmpty());
        if (!completed.isEmpty()) worldDelta.addProperty("blocks_completed", completed.size());
        fail(code);
    }

    private void finishData() {
        com.google.gson.JsonArray indices = new com.google.gson.JsonArray();
        for (int value : completed) indices.add(value);
        data.add("completed_indices", indices);
        data.add("placed_indices", indices.deepCopy());
        data.addProperty("completed", completed.size());
        data.addProperty("requested", placements.size());
    }

    @Override
    protected void onStop(ActionContext context) {
        context.motion().release(id, context.maid());
    }

    /** Tiny helper avoids coupling the action to registry lookups. */
    private static final class JsonObjectBuilder {
        static void addMissing(com.google.gson.JsonObject target, Item item) {
            com.google.gson.JsonObject missing = new com.google.gson.JsonObject();
            String id = net.minecraft.core.registries.BuiltInRegistries.ITEM.getKey(item).toString();
            missing.addProperty(id, 1);
            target.add("missing_materials", missing);
        }
    }
}
