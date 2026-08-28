package com.maidaibridge.craft;

import com.github.tartaricacid.touhoulittlemaid.entity.passive.EntityMaid;
import com.maidaibridge.inventory.MaidInventoryService;
import net.minecraft.core.BlockPos;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.crafting.CraftingRecipe;
import net.minecraft.world.item.crafting.Ingredient;
import net.minecraft.world.item.crafting.Recipe;
import net.minecraft.world.level.block.Blocks;
import net.minecraftforge.items.IItemHandlerModifiable;

import java.util.ArrayList;
import java.util.Collection;
import java.util.List;

/** Strict survival crafting: CraftingRecipe only, full simulation before mutation. */
public final class MaidCraftService {
    public record CraftResult(boolean ok, String code, int requested, int produced) {}

    private final MaidInventoryService inventory;

    public MaidCraftService(MaidInventoryService inventory) {
        this.inventory = inventory;
    }

    public CraftResult craft(EntityMaid maid, ServerLevel level, Item target, int requested) {
        int wanted = Math.max(1, requested);
        CraftingRecipe recipe = findRecipe(level, target);
        if (recipe == null) return new CraftResult(false, "RECIPE_NOT_FOUND", wanted, 0);
        if (!recipe.canCraftInDimensions(2, 2) && !nearCraftingTable(level, maid.blockPosition())) {
            return new CraftResult(false, "NO_WORKSTATION", wanted, 0);
        }
        ItemStack output = recipe.getResultItem(level.registryAccess());
        if (output.isEmpty() || !output.is(target)) {
            return new CraftResult(false, "RECIPE_NOT_FOUND", wanted, 0);
        }
        int loops = Math.max(1, (int) Math.ceil(wanted / (double) output.getCount()));
        IItemHandlerModifiable handler = inventory.handler(maid);
        int[] remaining = new int[handler.getSlots()];
        for (int slot = 0; slot < remaining.length; slot++) remaining[slot] = handler.getStackInSlot(slot).getCount();

        List<Integer> removalSlots = new ArrayList<>();
        List<ItemStack> additions = new ArrayList<>();
        for (int loop = 0; loop < loops; loop++) {
            List<Integer> matches = matchIngredients(handler, remaining, recipe.getIngredients());
            if (matches == null) return new CraftResult(false, "NO_MATERIAL", wanted, 0);
            for (int slot : matches) {
                removalSlots.add(slot);
                ItemStack ingredient = handler.getStackInSlot(slot);
                if (ingredient.hasCraftingRemainingItem()) additions.add(ingredient.getCraftingRemainingItem());
            }
            additions.add(output.copy());
        }
        if (!canFitAfterRemovals(handler, removalSlots, additions)) {
            return new CraftResult(false, "INVENTORY_FULL", wanted, 0);
        }

        List<ItemStack> consumed = new ArrayList<>();
        for (int slot : removalSlots) {
            ItemStack stack = handler.extractItem(slot, 1, false);
            if (stack.isEmpty()) {
                rollback(maid, consumed);
                return new CraftResult(false, "INVENTORY_CHANGED", wanted, 0);
            }
            consumed.add(stack);
        }
        for (ItemStack stack : additions) {
            if (!inventory.insert(maid, stack)) {
                // Preflight should make this impossible. Preserve all items rather than
                // silently losing them if another mod changes the inventory mid-action.
                maid.spawnAtLocation(stack);
            }
        }
        return new CraftResult(true, "CRAFTED", wanted, loops * output.getCount());
    }

    private CraftingRecipe findRecipe(ServerLevel level, Item target) {
        Collection<Recipe<?>> recipes = level.getRecipeManager().getRecipes();
        for (Recipe<?> recipe : recipes) {
            if (!(recipe instanceof CraftingRecipe crafting)) continue;
            ItemStack result = crafting.getResultItem(level.registryAccess());
            if (result.is(target)) return crafting;
        }
        return null;
    }

    private static List<Integer> matchIngredients(
            IItemHandlerModifiable handler, int[] remaining, List<Ingredient> ingredients
    ) {
        List<Integer> result = new ArrayList<>();
        for (Ingredient ingredient : ingredients) {
            if (ingredient.isEmpty()) continue;
            int match = -1;
            for (int slot = 0; slot < remaining.length; slot++) {
                if (remaining[slot] > 0 && ingredient.test(handler.getStackInSlot(slot))) {
                    match = slot;
                    break;
                }
            }
            if (match < 0) return null;
            remaining[match]--;
            result.add(match);
        }
        return result;
    }

    private static boolean canFitAfterRemovals(
            IItemHandlerModifiable handler, List<Integer> removals, List<ItemStack> additions
    ) {
        ItemStack[] virtual = new ItemStack[handler.getSlots()];
        for (int slot = 0; slot < virtual.length; slot++) virtual[slot] = handler.getStackInSlot(slot).copy();
        for (int slot : removals) virtual[slot].shrink(1);
        for (ItemStack addition : additions) {
            ItemStack remaining = addition.copy();
            for (int slot = 0; slot < virtual.length && !remaining.isEmpty(); slot++) {
                if (!handler.isItemValid(slot, remaining)) continue;
                ItemStack current = virtual[slot];
                int limit = Math.min(handler.getSlotLimit(slot), remaining.getMaxStackSize());
                if (current.isEmpty()) {
                    int moved = Math.min(limit, remaining.getCount());
                    virtual[slot] = remaining.copyWithCount(moved);
                    remaining.shrink(moved);
                } else if (ItemStack.isSameItemSameTags(current, remaining)) {
                    int moved = Math.min(remaining.getCount(), Math.max(0, Math.min(limit, current.getMaxStackSize()) - current.getCount()));
                    current.grow(moved);
                    remaining.shrink(moved);
                }
            }
            if (!remaining.isEmpty()) return false;
        }
        return true;
    }

    private void rollback(EntityMaid maid, List<ItemStack> stacks) {
        for (ItemStack stack : stacks) {
            if (!inventory.insert(maid, stack)) maid.spawnAtLocation(stack);
        }
    }

    private static boolean nearCraftingTable(ServerLevel level, BlockPos center) {
        for (BlockPos pos : BlockPos.betweenClosed(center.offset(-4, -2, -4), center.offset(4, 2, 4))) {
            if (level.getBlockState(pos).is(Blocks.CRAFTING_TABLE)) return true;
        }
        return false;
    }
}
