package com.maidaibridge.observe;

import com.github.tartaricacid.touhoulittlemaid.entity.passive.EntityMaid;
import com.maidaibridge.BridgeConfig;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.Mob;
import net.minecraft.world.entity.MobCategory;
import net.minecraft.world.entity.monster.Enemy;

import java.util.Locale;
import java.util.UUID;

public final class ThreatClassifier {
    public boolean hostile(Entity entity, EntityMaid maid) {
        if (entity == maid || !entity.isAlive()) return false;
        if (entity instanceof Enemy) return true;
        if (entity.getType().getCategory() == MobCategory.MONSTER) return true;
        if (entity instanceof Mob mob && mob.getTarget() == maid) return true;

        if (maid.getPersistentData().hasUUID("MaidAI.RecentAttacker")) {
            UUID attacker = maid.getPersistentData().getUUID("MaidAI.RecentAttacker");
            long attackerTick = maid.getPersistentData().getLong("MaidAI.RecentAttackerTick");
            if (attacker.equals(entity.getUUID()) && maid.level().getGameTime() - attackerTick <= 200) return true;
        }
        String id = BuiltInRegistries.ENTITY_TYPE.getKey(entity.getType()).toString().toLowerCase(Locale.ROOT);
        for (String configured : BridgeConfig.EXTRA_HOSTILE_ENTITY_TYPES.get().split(",")) {
            if (!configured.isBlank() && id.equals(configured.trim().toLowerCase(Locale.ROOT))) return true;
        }
        return false;
    }
}
