package com.maidaibridge;

import com.maidaibridge.command.MaidAiCommand;import com.maidaibridge.controller.MaidAiController;
import com.mojang.logging.LogUtils;import net.minecraftforge.common.MinecraftForge;import net.minecraftforge.event.RegisterCommandsEvent;import net.minecraftforge.event.TickEvent;import net.minecraftforge.event.server.ServerStartedEvent;import net.minecraftforge.event.server.ServerStoppingEvent;import net.minecraftforge.eventbus.api.SubscribeEvent;import net.minecraftforge.fml.ModLoadingContext;import net.minecraftforge.fml.common.Mod;import net.minecraftforge.fml.config.ModConfig;import org.slf4j.Logger;

@Mod(MaidAiBridgeMod.MOD_ID)
public final class MaidAiBridgeMod {
    public static final String MOD_ID="maid_ai_bridge";public static final String VERSION="0.3.0";public static final Logger LOGGER= LogUtils.getLogger();public static final MaidAiController CONTROLLER=new MaidAiController();
    public MaidAiBridgeMod(){ModLoadingContext.get().registerConfig(ModConfig.Type.COMMON,BridgeConfig.SPEC);MinecraftForge.EVENT_BUS.register(this);}
    @SubscribeEvent public void onServerStarted(ServerStartedEvent event){CONTROLLER.start(event.getServer());}
    @SubscribeEvent public void onServerStopping(ServerStoppingEvent event){CONTROLLER.stop();}
    @SubscribeEvent public void onServerTick(TickEvent.ServerTickEvent event){if(event.phase==TickEvent.Phase.END)CONTROLLER.tick();}
    @SubscribeEvent public void onRegisterCommands(RegisterCommandsEvent event){MaidAiCommand.register(event.getDispatcher());}
}
