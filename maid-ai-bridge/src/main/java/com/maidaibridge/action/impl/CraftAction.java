package com.maidaibridge.action.impl;

import com.maidaibridge.action.AbstractMaidAction;
import com.maidaibridge.action.ActionContext;
import com.maidaibridge.action.MaidActionSafety;
import com.maidaibridge.craft.MaidCraftService;
import net.minecraft.world.item.Item;

public final class CraftAction extends AbstractMaidAction {
    private final MaidCraftService service;
    private final Item item;
    private final int count;
    private int workTicks = 10;

    public CraftAction(String requestId, int timeout, MaidCraftService service, Item item, int count) {
        super(requestId, "craft", timeout);
        this.service = service;
        this.item = item;
        this.count = Math.max(1, count);
    }

    @Override
    protected void onStart(ActionContext context) {
        MaidActionSafety.requireServerThread(context);
    }

    @Override
    protected void onTick(ActionContext context) {
        if (--workTicks > 0) return;
        MaidCraftService.CraftResult result = service.craft(context.maid(), context.level(), item, count);
        data.addProperty("requested", result.requested());
        data.addProperty("produced", result.produced());
        if (result.ok()) {
            worldDelta.addProperty("crafted_count", result.produced());
            succeed(result.code());
        } else {
            fail(result.code());
        }
    }
}
