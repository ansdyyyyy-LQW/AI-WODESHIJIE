package com.maidaibridge.task;

import com.github.tartaricacid.touhoulittlemaid.api.task.IMaidTask;
import com.github.tartaricacid.touhoulittlemaid.entity.passive.EntityMaid;
import com.google.common.collect.ImmutableList;
import com.mojang.datafixers.util.Pair;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.sounds.SoundEvent;
import net.minecraft.world.entity.ai.behavior.BehaviorControl;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;

import javax.annotation.Nullable;
import java.util.List;

public final class MaidAutonomousTask implements IMaidTask {
    public static final ResourceLocation UID = new ResourceLocation("maid_ai_bridge", "autonomous");
    @Override public ResourceLocation getUid() { return UID; }
    @Override public ItemStack getIcon() { return new ItemStack(Items.COMPASS); }
    @Nullable @Override public SoundEvent getAmbientSound(EntityMaid maid) { return null; }
    @Override public List<Pair<Integer, BehaviorControl<? super EntityMaid>>> createBrainTasks(EntityMaid maid) {
        return ImmutableList.of(Pair.of(5, new ExternalAgentBehavior()));
    }
    @Override public boolean enableLookAndRandomWalk(EntityMaid maid) { return false; }
    @Override public boolean enablePanic(EntityMaid maid) { return false; }
    @Override public boolean enableEating(EntityMaid maid) { return false; }
    @Override public String getMaidActionSummary() { return "External Agent has exclusive body control"; }
}
