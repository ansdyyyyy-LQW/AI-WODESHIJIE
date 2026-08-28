package com.maidaibridge.action.impl;

import com.maidaibridge.action.AbstractMaidAction;
import com.maidaibridge.action.ActionContext;
import net.minecraft.world.food.FoodProperties;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;

public final class EatAction extends AbstractMaidAction {
    private final Item requestedItem;

    public EatAction(String requestId, int timeout, Item requestedItem) {
        super(requestId, "eat", timeout);
        this.requestedItem = requestedItem;
    }

    @Override
    protected void onTick(ActionContext context) {
        var slots = context.inventory().matchingSlots(
                context.maid(),
                stack -> !stack.isEmpty() && stack.isEdible()
                        && (requestedItem == null || stack.is(requestedItem))
        );
        if (slots.isEmpty()) {
            fail("NO_FOOD");
            return;
        }
        int slot = slots.get(0);
        ItemStack one = context.inventory().handler(context.maid()).extractItem(slot, 1, false);
        FoodProperties food = one.getFoodProperties(context.maid());
        if (food == null) {
            context.inventory().insert(context.maid(), one);
            fail("NO_FOOD");
            return;
        }
        context.maid().setHunger(Math.min(20, context.maid().getHunger() + food.getNutrition()));
        if (food.getNutrition() > 0) context.maid().heal(Math.max(1, food.getNutrition() / 2.0f));
        data.addProperty("nutrition", food.getNutrition());
        succeed("ATE");
    }
}
