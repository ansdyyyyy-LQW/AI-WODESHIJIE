package com.maidaibridge.inventory;

import com.github.tartaricacid.touhoulittlemaid.entity.passive.EntityMaid;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraftforge.items.IItemHandler;
import net.minecraftforge.items.IItemHandlerModifiable;

import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

public final class MaidInventoryService {
    public IItemHandlerModifiable handler(EntityMaid maid) { return maid.getAvailableInv(true); }

    public JsonArray snapshot(EntityMaid maid) {
        JsonArray result = new JsonArray();
        IItemHandler handler = handler(maid);
        for (int slot = 0; slot < handler.getSlots(); slot++) {
            ItemStack stack = handler.getStackInSlot(slot);
            if (stack.isEmpty()) continue;
            JsonObject row = new JsonObject(); row.addProperty("slot", slot);
            row.addProperty("id", BuiltInRegistries.ITEM.getKey(stack.getItem()).toString());
            row.addProperty("count", stack.getCount());
            result.add(row);
        }
        return result;
    }

    public int count(EntityMaid maid, Item item) {
        int count = 0; IItemHandler handler = handler(maid);
        for (int slot = 0; slot < handler.getSlots(); slot++) {
            ItemStack stack = handler.getStackInSlot(slot);
            if (stack.is(item)) count += stack.getCount();
        }
        return count;
    }

    public Optional<Item> resolveItem(String id) {
        ResourceLocation key = ResourceLocation.tryParse(id);
        if (key == null || !BuiltInRegistries.ITEM.containsKey(key)) return Optional.empty();
        Item item = BuiltInRegistries.ITEM.get(key);
        return Optional.ofNullable(item);
    }

    public ItemStack extract(EntityMaid maid, Item item, int amount, boolean simulate) {
        IItemHandlerModifiable handler = handler(maid);
        ItemStack combined = ItemStack.EMPTY;
        int remaining = amount;
        for (int slot = 0; slot < handler.getSlots() && remaining > 0; slot++) {
            ItemStack current = handler.getStackInSlot(slot);
            if (!current.is(item)) continue;
            ItemStack extracted = handler.extractItem(slot, remaining, simulate);
            if (extracted.isEmpty()) continue;
            if (combined.isEmpty()) combined = extracted.copy(); else combined.grow(extracted.getCount());
            remaining -= extracted.getCount();
        }
        return combined;
    }

    public boolean insert(EntityMaid maid, ItemStack stack) {
        ItemStack remaining = stack.copy(); IItemHandlerModifiable handler = handler(maid);
        for (int slot = 0; slot < handler.getSlots() && !remaining.isEmpty(); slot++) {
            remaining = handler.insertItem(slot, remaining, false);
        }
        return remaining.isEmpty();
    }

    public boolean hasSpace(EntityMaid maid, ItemStack stack) {
        ItemStack remaining = stack.copy(); IItemHandlerModifiable handler = handler(maid);
        for (int slot = 0; slot < handler.getSlots() && !remaining.isEmpty(); slot++) {
            remaining = handler.insertItem(slot, remaining, true);
        }
        return remaining.isEmpty();
    }

    public List<Integer> matchingSlots(EntityMaid maid, java.util.function.Predicate<ItemStack> predicate) {
        List<Integer> result = new ArrayList<>(); IItemHandler handler = handler(maid);
        for (int slot=0;slot<handler.getSlots();slot++) if (predicate.test(handler.getStackInSlot(slot))) result.add(slot);
        return result;
    }
    public int capacity(EntityMaid maid, ItemStack stack) {
        if (stack.isEmpty()) return 0;
        int capacity = 0;
        IItemHandlerModifiable handler = handler(maid);
        for (int slot = 0; slot < handler.getSlots(); slot++) {
            ItemStack current = handler.getStackInSlot(slot);
            if (current.isEmpty()) {
                if (handler.isItemValid(slot, stack)) capacity += Math.min(stack.getMaxStackSize(), handler.getSlotLimit(slot));
            } else if (ItemStack.isSameItemSameTags(current, stack)) {
                capacity += Math.max(0, Math.min(current.getMaxStackSize(), handler.getSlotLimit(slot)) - current.getCount());
            }
        }
        return capacity;
    }

    public int available(EntityMaid maid, Item item) { return count(maid, item); }

}
