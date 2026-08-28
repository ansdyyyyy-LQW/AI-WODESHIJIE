package com.maidaibridge.task;

import com.github.tartaricacid.touhoulittlemaid.entity.passive.EntityMaid;
import com.google.common.collect.ImmutableMap;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.entity.ai.behavior.Behavior;

public final class ExternalAgentBehavior extends Behavior<EntityMaid> {
    public ExternalAgentBehavior() { super(ImmutableMap.of()); }
    @Override protected boolean checkExtraStartConditions(ServerLevel level, EntityMaid maid) { return true; }
    @Override protected boolean canStillUse(ServerLevel level, EntityMaid maid, long gameTime) { return true; }
    @Override protected boolean timedOut(long gameTime) { return false; }
    @Override protected void tick(ServerLevel level, EntityMaid maid, long gameTime) {
        // Body mutation is centralized in the server-tick MaidAiController.
        maid.getLookControl().setLookAt(maid.getLookAngle().add(maid.position()));
    }
}
