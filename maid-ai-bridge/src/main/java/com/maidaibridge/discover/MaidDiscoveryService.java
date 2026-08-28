package com.maidaibridge.discover;

import com.github.tartaricacid.touhoulittlemaid.entity.passive.EntityMaid;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.LivingEntity;

import java.util.Optional;
import java.util.UUID;

public final class MaidDiscoveryService {
    public JsonArray players(MinecraftServer server) {
        JsonArray array = new JsonArray();
        for (ServerPlayer player : server.getPlayerList().getPlayers()) {
            JsonObject item = new JsonObject();
            item.addProperty("name", player.getGameProfile().getName());
            item.addProperty("uuid", player.getUUID().toString());
            array.add(item);
        }
        return array;
    }

    public Optional<EntityMaid> find(MinecraftServer server, UUID id) {
        for (ServerLevel level : server.getAllLevels()) {
            Entity entity = level.getEntity(id);
            if (entity instanceof EntityMaid maid && maid.isAlive()) return Optional.of(maid);
        }
        return Optional.empty();
    }

    public JsonArray discover(MinecraftServer server, String ownerFilter) {
        JsonArray array = new JsonArray();
        UUID requiredOwner = null;
        if (ownerFilter != null && !ownerFilter.isBlank()) {
            try { requiredOwner = UUID.fromString(ownerFilter); }
            catch (IllegalArgumentException ignored) { return array; }
        }
        for (ServerLevel level : server.getAllLevels()) {
            for (Entity entity : level.getAllEntities()) {
                if (!(entity instanceof EntityMaid maid) || !maid.isAlive()) continue;
                UUID ownerId = maid.getOwnerUUID();
                if (requiredOwner != null && !requiredOwner.equals(ownerId)) continue;
                JsonObject item = new JsonObject();
                item.addProperty("uuid", maid.getUUID().toString());
                item.addProperty("entity_id", maid.getId());
                item.addProperty("name", maid.getName().getString());
                if (ownerId != null) item.addProperty("owner_uuid", ownerId.toString());
                LivingEntity owner = maid.getOwner();
                item.addProperty("owner_name", owner == null ? "" : owner.getName().getString());
                item.addProperty("dimension", level.dimension().location().toString());
                JsonArray position = new JsonArray();
                position.add(maid.getX());
                position.add(maid.getY());
                position.add(maid.getZ());
                item.add("position", position);
                item.addProperty("distance_to_owner", owner == null ? -1 : Math.sqrt(maid.distanceToSqr(owner)));
                item.addProperty("loaded", true);
                array.add(item);
            }
        }
        return array;
    }
}
