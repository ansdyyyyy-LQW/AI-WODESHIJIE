package com.maidaibridge.smelt;

import com.github.tartaricacid.touhoulittlemaid.entity.passive.EntityMaid;
import com.maidaibridge.inventory.MaidInventoryService;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.level.block.entity.AbstractFurnaceBlockEntity;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.crafting.AbstractCookingRecipe;
import net.minecraft.world.item.crafting.Recipe;
import net.minecraft.world.item.crafting.RecipeType;
import net.minecraftforge.common.ForgeHooks;

import java.util.Collection;

public final class MaidSmeltService {
    public record CookingPlan(boolean valid, int cookTime, int outputPerInput) {}

    private final MaidInventoryService inventory;

    public MaidSmeltService(MaidInventoryService inventory) {
        this.inventory = inventory;
    }

    public CookingPlan validateRecipe(ServerLevel level, Item input, Item expectedOutput) {
        Collection<Recipe<?>> recipes = level.getRecipeManager().getRecipes();
        ItemStack sample = new ItemStack(input);
        for (Recipe<?> recipe : recipes) {
            if (!(recipe instanceof AbstractCookingRecipe cooking)) continue;
            if (recipe.getType() != RecipeType.SMELTING) continue;
            if (cooking.getIngredients().isEmpty() || !cooking.getIngredients().get(0).test(sample)) continue;
            ItemStack output = cooking.getResultItem(level.registryAccess());
            if (!output.is(expectedOutput)) continue;
            return new CookingPlan(true, Math.max(1, cooking.getCookingTime()), Math.max(1, output.getCount()));
        }
        return new CookingPlan(false, 0, 0);
    }

    public int insertInput(EntityMaid maid, AbstractFurnaceBlockEntity furnace, Item input, int count) {
        ItemStack current = furnace.getItem(0);
        int capacity = current.isEmpty() ? Math.min(64, new ItemStack(input).getMaxStackSize())
                : (current.is(input) ? Math.max(0, current.getMaxStackSize() - current.getCount()) : 0);
        int amount = Math.min(Math.max(1, count), Math.min(capacity, inventory.count(maid, input)));
        if (amount <= 0) return 0;
        ItemStack stack = inventory.extract(maid, input, amount, false);
        if (stack.isEmpty()) return 0;
        if (current.isEmpty()) furnace.setItem(0, stack);
        else {
            current.grow(stack.getCount());
            furnace.setItem(0, current);
        }
        furnace.setChanged();
        return stack.getCount();
    }

    public void rollbackInput(EntityMaid maid, AbstractFurnaceBlockEntity furnace, Item input, int count) {
        ItemStack current = furnace.getItem(0);
        if (!current.is(input) || count <= 0) return;
        int amount = Math.min(count, current.getCount());
        ItemStack returned = current.copyWithCount(amount);
        current.shrink(amount);
        furnace.setItem(0, current);
        if (!inventory.insert(maid, returned)) maid.spawnAtLocation(returned);
        furnace.setChanged();
    }

    public boolean ensureFuel(
            EntityMaid maid, AbstractFurnaceBlockEntity furnace, int requiredBurnTicks
    ) {
        ItemStack current = furnace.getItem(1);
        int existing = current.isEmpty() ? 0
                : ForgeHooks.getBurnTime(current, RecipeType.SMELTING) * current.getCount();
        if (existing >= requiredBurnTicks) return true;
        int missing = requiredBurnTicks - existing;
        var slots = inventory.matchingSlots(
                maid,
                stack -> !stack.isEmpty() && ForgeHooks.getBurnTime(stack, RecipeType.SMELTING) > 0
                        && (current.isEmpty() || ItemStack.isSameItemSameTags(current, stack))
        );
        if (slots.isEmpty()) return false;
        ItemStack sample = inventory.handler(maid).getStackInSlot(slots.get(0));
        int burn = ForgeHooks.getBurnTime(sample, RecipeType.SMELTING);
        if (burn <= 0) return false;
        int neededItems = (int) Math.ceil(missing / (double) burn);
        int capacity = current.isEmpty() ? sample.getMaxStackSize() : current.getMaxStackSize() - current.getCount();
        int available = inventory.count(maid, sample.getItem());
        if (neededItems > capacity || neededItems > available) return false;
        ItemStack fuel = inventory.extract(maid, sample.getItem(), neededItems, false);
        if (fuel.getCount() != neededItems) {
            if (!fuel.isEmpty()) inventory.insert(maid, fuel);
            return false;
        }
        if (current.isEmpty()) furnace.setItem(1, fuel);
        else {
            current.grow(fuel.getCount());
            furnace.setItem(1, current);
        }
        furnace.setChanged();
        return true;
    }

    public int outputCount(AbstractFurnaceBlockEntity furnace, Item expected) {
        ItemStack output = furnace.getItem(2);
        return output.is(expected) ? output.getCount() : 0;
    }

    public int collect(EntityMaid maid, AbstractFurnaceBlockEntity furnace, Item expected, int amount) {
        ItemStack output = furnace.getItem(2);
        if (output.isEmpty() || !output.is(expected) || amount <= 0) return 0;
        int takeCount = Math.min(amount, output.getCount());
        ItemStack take = output.copyWithCount(takeCount);
        int capacity = inventory.capacity(maid, take);
        takeCount = Math.min(takeCount, capacity);
        if (takeCount <= 0) return -1;
        take = output.copyWithCount(takeCount);
        if (!inventory.insert(maid, take)) return -1;
        output.shrink(takeCount);
        furnace.setItem(2, output);
        furnace.setChanged();
        return takeCount;
    }
}
