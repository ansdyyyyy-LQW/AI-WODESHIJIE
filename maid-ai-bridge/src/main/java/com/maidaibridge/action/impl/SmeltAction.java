package com.maidaibridge.action.impl;

import com.maidaibridge.action.AbstractMaidAction;
import com.maidaibridge.action.ActionContext;
import com.maidaibridge.action.MaidActionSafety;
import com.maidaibridge.controller.MotionPriority;
import com.maidaibridge.smelt.MaidSmeltService;
import net.minecraft.core.BlockPos;
import net.minecraft.world.item.Item;
import net.minecraft.world.level.block.entity.AbstractFurnaceBlockEntity;

public final class SmeltAction extends AbstractMaidAction {
    private final MaidSmeltService service;
    private final BlockPos furnacePos;
    private final Item input;
    private final Item output;
    private final int requested;
    private boolean inserted;
    private int insertedInputs;
    private int collected;
    private int initialOutput;
    private long lastPath;

    public SmeltAction(
            String requestId, int timeout, MaidSmeltService service, BlockPos furnacePos,
            Item input, Item output, int requested
    ) {
        super(requestId, "smelt", timeout);
        this.service = service;
        this.furnacePos = furnacePos;
        this.input = input;
        this.output = output;
        this.requested = Math.max(1, requested);
    }

    @Override
    protected void onStart(ActionContext context) {
        MaidActionSafety.requireServerThread(context);
        if (!MaidActionSafety.loaded(context, furnacePos)) {
            fail("CHUNK_NOT_LOADED");
            return;
        }
        if (!(context.level().getBlockEntity(furnacePos) instanceof AbstractFurnaceBlockEntity)) {
            fail("NO_WORKSTATION");
            return;
        }
        MaidSmeltService.CookingPlan plan = service.validateRecipe(context.level(), input, output);
        if (!plan.valid()) {
            fail("RECIPE_NOT_FOUND");
            return;
        }
        if (context.motion().acquire(id, MotionPriority.TASK, context.gameTick()).isEmpty()) {
            fail("MOTION_BUSY");
        }
    }

    @Override
    protected void onTick(ActionContext context) {
        if (!(context.level().getBlockEntity(furnacePos) instanceof AbstractFurnaceBlockEntity furnace)) {
            fail("NO_WORKSTATION");
            return;
        }
        if (!MaidActionSafety.within(context, furnacePos, 3.5)) {
            if (context.gameTick() - lastPath >= 20) {
                context.motion().moveTo(id, context.maid(), furnacePos.getX() + .5, furnacePos.getY(), furnacePos.getZ() + .5, .8);
                lastPath = context.gameTick();
            }
            return;
        }
        context.motion().release(id, context.maid());
        String safety = MaidActionSafety.validateCloseInteraction(context, furnacePos, 3.5);
        if (!safety.isEmpty()) {
            fail(safety);
            return;
        }
        MaidSmeltService.CookingPlan plan = service.validateRecipe(context.level(), input, output);
        if (!plan.valid()) {
            fail("RECIPE_NOT_FOUND");
            return;
        }
        if (!inserted) {
            initialOutput = service.outputCount(furnace, output);
            int inputsNeeded = (int) Math.ceil(requested / (double) plan.outputPerInput());
            insertedInputs = service.insertInput(context.maid(), furnace, input, inputsNeeded);
            if (insertedInputs < inputsNeeded) {
                if (insertedInputs > 0) service.rollbackInput(context.maid(), furnace, input, insertedInputs);
                fail("NO_MATERIAL");
                return;
            }
            if (!service.ensureFuel(context.maid(), furnace, plan.cookTime() * insertedInputs)) {
                service.rollbackInput(context.maid(), furnace, input, insertedInputs);
                fail("NO_FUEL");
                return;
            }
            inserted = true;
        }

        int currentlyProduced = Math.max(0, service.outputCount(furnace, output) - initialOutput);
        if (currentlyProduced > 0 && collected < requested) {
            int taken = service.collect(context.maid(), furnace, output, Math.min(currentlyProduced, requested - collected));
            if (taken < 0) {
                fail("INVENTORY_FULL");
                return;
            }
            collected += taken;
            initialOutput = Math.max(0, initialOutput - Math.max(0, taken - currentlyProduced));
        }
        data.addProperty("requested", requested);
        data.addProperty("collected", collected);
        if (collected >= requested) {
            worldDelta.addProperty("smelted_count", collected);
            succeed("SMELTED");
        }
    }

    @Override
    protected void onStop(ActionContext context) {
        context.motion().release(id, context.maid());
    }
}
