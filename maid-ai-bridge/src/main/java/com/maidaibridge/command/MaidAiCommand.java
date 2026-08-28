package com.maidaibridge.command;

import com.github.tartaricacid.touhoulittlemaid.entity.passive.EntityMaid;
import com.maidaibridge.MaidAiBridgeMod;
import com.mojang.brigadier.CommandDispatcher;
import net.minecraft.commands.CommandSourceStack;import net.minecraft.commands.Commands;import net.minecraft.network.chat.Component;import net.minecraft.server.level.ServerPlayer;

import java.util.Comparator;

public final class MaidAiCommand {
    public static void register(CommandDispatcher<CommandSourceStack> dispatcher){dispatcher.register(Commands.literal("maidai").requires(s->s.hasPermission(2))
        .then(Commands.literal("status").executes(ctx->{var c=MaidAiBridgeMod.CONTROLLER;ctx.getSource().sendSuccess(()->Component.literal("Bridge="+(c.connected()?"connected":"disconnected")+", maid="+c.boundMaid()),false);return 1;}))
        .then(Commands.literal("reconnect").executes(ctx->{MaidAiBridgeMod.CONTROLLER.reconnect();ctx.getSource().sendSuccess(()->Component.literal("Maid AI Bridge reconnect requested"),false);return 1;}))
        .then(Commands.literal("stop").executes(ctx->{MaidAiBridgeMod.CONTROLLER.safeIdle("command");ctx.getSource().sendSuccess(()->Component.literal("Maid AI stopped safely"),false);return 1;}))
        .then(Commands.literal("unbind").executes(ctx->{MaidAiBridgeMod.CONTROLLER.unbind();ctx.getSource().sendSuccess(()->Component.literal("Maid unbound"),false);return 1;}))
        .then(Commands.literal("bind").executes(ctx->{ServerPlayer player=ctx.getSource().getPlayerOrException();EntityMaid maid=player.serverLevel().getEntitiesOfClass(EntityMaid.class,player.getBoundingBox().inflate(16),m->m.isAlive()&&m.isOwnedBy(player)).stream().min(Comparator.comparingDouble(player::distanceToSqr)).orElse(null);if(maid==null){ctx.getSource().sendFailure(Component.literal("附近没有属于你的女仆"));return 0;}MaidAiBridgeMod.CONTROLLER.bind(maid.getUUID(),player.getUUID().toString());ctx.getSource().sendSuccess(()->Component.literal("已绑定 "+maid.getName().getString()),false);return 1;}))
    );}
    private MaidAiCommand(){}
}
