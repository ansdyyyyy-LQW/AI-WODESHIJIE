package com.maidaibridge.controller;

import com.maidaibridge.MaidAiBridgeMod;
import net.minecraft.server.level.ServerLevel;
import net.minecraftforge.event.entity.living.LivingDeathEvent;
import net.minecraftforge.event.entity.living.LivingHurtEvent;
import net.minecraftforge.event.level.LevelEvent;
import net.minecraftforge.eventbus.api.SubscribeEvent;
import net.minecraftforge.fml.common.Mod;

@Mod.EventBusSubscriber(modid = MaidAiBridgeMod.MOD_ID, bus = Mod.EventBusSubscriber.Bus.FORGE)
public final class MaidAiForgeEvents {
    @SubscribeEvent
    public static void onLivingHurt(LivingHurtEvent event) {
        MaidAiBridgeMod.CONTROLLER.onLivingHurt(event.getEntity(), event.getSource(), event.getAmount());
    }

    @SubscribeEvent
    public static void onLivingDeath(LivingDeathEvent event) {
        MaidAiBridgeMod.CONTROLLER.onLivingDeath(event.getEntity(), event.getSource());
    }

    @SubscribeEvent
    public static void onLevelSave(LevelEvent.Save event) {
        if (event.getLevel() instanceof ServerLevel level) {
            MaidAiBridgeMod.CONTROLLER.onWorldSave(level);
        }
    }

    private MaidAiForgeEvents() {}
}
