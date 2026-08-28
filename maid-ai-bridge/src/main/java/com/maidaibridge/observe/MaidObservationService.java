package com.maidaibridge.observe;

import com.github.tartaricacid.touhoulittlemaid.entity.passive.EntityMaid;
import com.google.gson.JsonArray;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import com.maidaibridge.BridgeConfig;
import com.maidaibridge.action.ActionEngine;
import com.maidaibridge.inventory.MaidInventoryService;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.util.Mth;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.Mob;
import net.minecraft.world.entity.item.ItemEntity;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.level.block.BasePressurePlateBlock;
import net.minecraft.world.level.block.ButtonBlock;
import net.minecraft.world.level.block.DoorBlock;
import net.minecraft.world.level.block.FenceGateBlock;
import net.minecraft.world.level.block.LeverBlock;
import net.minecraft.world.level.block.TrapDoorBlock;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.properties.Property;
import net.minecraft.world.phys.AABB;

import java.lang.ref.WeakReference;
import java.util.Comparator;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/** Produces only information that the real maid can currently observe. */
public final class MaidObservationService {
    public enum EntityPresence {
        OBSERVABLE,
        PRESENT_NOT_OBSERVABLE,
        DEAD,
        CONFIRMED_GONE,
        UNKNOWN
    }

    private static final int MAX_TRACKED_ENTITIES = 512;
    private final MaidInventoryService inventory;
    private final VisibilityService visibility = new VisibilityService();
    private final ThreatClassifier threats = new ThreatClassifier();
    private final LinkedHashMap<UUID, WeakReference<Entity>> trackedEntities =
            new LinkedHashMap<>() {
                @Override
                protected boolean removeEldestEntry(Map.Entry<UUID, WeakReference<Entity>> eldest) {
                    return size() > MAX_TRACKED_ENTITIES;
                }
            };

    public MaidObservationService(MaidInventoryService inventory) {
        this.inventory = inventory;
    }

    public JsonObject snapshot(EntityMaid maid, ServerLevel level, ActionEngine actions) {
        JsonObject result = new JsonObject();
        result.addProperty("maid_name", maid.getName().getString());
        result.addProperty("dimension", level.dimension().location().toString());
        long dayTime = level.getDayTime();
        result.addProperty("day", dayTime / 24000L);
        result.addProperty("time_of_day", dayTime % 24000L);
        JsonObject pos = new JsonObject();
        pos.addProperty("x", maid.getX());
        pos.addProperty("y", maid.getY());
        pos.addProperty("z", maid.getZ());
        result.add("position", pos);
        result.addProperty("yaw", maid.getYRot());
        result.addProperty("pitch", maid.getXRot());
        result.addProperty("health", maid.getHealth());
        result.addProperty("max_health", maid.getMaxHealth());
        result.addProperty("hunger", maid.getHunger());
        result.addProperty("air", maid.getAirSupply());
        result.addProperty("on_fire", maid.isOnFire());
        result.addProperty("in_water", maid.isInWater());
        result.addProperty("weather", level.isThundering() ? "THUNDER" : level.isRaining() ? "RAIN" : "CLEAR");
        result.addProperty("biome", level.getBiome(maid.blockPosition()).unwrapKey()
                .map(key -> key.location().toString()).orElse("unknown"));
        result.addProperty("main_hand_item", BuiltInRegistries.ITEM.getKey(maid.getMainHandItem().getItem()).toString());
        result.addProperty("off_hand_item", BuiltInRegistries.ITEM.getKey(maid.getOffhandItem().getItem()).toString());
        if (maid.getOwnerUUID() != null) result.addProperty("owner_uuid", maid.getOwnerUUID().toString());
        result.addProperty("navigation_in_progress", maid.getNavigation().isInProgress());
        result.add("inventory", inventory.snapshot(maid));
        result.add("nearby_entities", nearbyEntities(maid, level));
        result.add("entity_presence", entityPresenceSnapshot(maid, level));
        result.add("visible_blocks", visibleBlocks(maid, level));
        result.add("current_action", actions.currentSummary());
        result.addProperty("reflex_state", actions.reflexState());
        return result;
    }

    public JsonObject inspectEntity(EntityMaid maid, ServerLevel level, UUID entityId) {
        JsonObject result = new JsonObject();
        Entity entity = level.getEntity(entityId);
        EntityPresence presence = entityPresence(maid, level, entityId);
        result.addProperty("uuid", entityId.toString());
        result.addProperty("presence", presence.name());
        result.addProperty("visible", presence == EntityPresence.OBSERVABLE);
        result.addProperty("confirmed_gone", presence == EntityPresence.DEAD
                || presence == EntityPresence.CONFIRMED_GONE);
        switch (presence) {
            case OBSERVABLE -> {
                appendEntity(result, maid, entity);
                result.addProperty("exists", true);
                result.addProperty("alive", true);
            }
            case PRESENT_NOT_OBSERVABLE -> {
                result.addProperty("exists", true);
                result.addProperty("alive", true);
            }
            case DEAD -> {
                result.addProperty("exists", false);
                result.addProperty("alive", false);
            }
            case CONFIRMED_GONE -> {
                result.addProperty("exists", false);
                result.addProperty("alive", false);
            }
            case UNKNOWN -> {
                result.add("exists", JsonNull.INSTANCE);
                result.add("alive", JsonNull.INSTANCE);
            }
        }
        return result;
    }

    public EntityPresence entityPresence(EntityMaid maid, ServerLevel level, UUID entityId) {
        Entity entity = level.getEntity(entityId);
        if (entity != null && entity != maid) {
            remember(entity);
            if (!entity.isAlive()) return EntityPresence.DEAD;
            int radius = BridgeConfig.ENTITY_OBSERVE_RADIUS.get();
            boolean observable = maid.distanceToSqr(entity) <= radius * radius
                    && maid.hasLineOfSight(entity);
            return observable ? EntityPresence.OBSERVABLE : EntityPresence.PRESENT_NOT_OBSERVABLE;
        }
        WeakReference<Entity> reference = trackedEntities.get(entityId);
        Entity known = reference == null ? null : reference.get();
        if (known == null) return EntityPresence.UNKNOWN;
        if (!known.isAlive()) return EntityPresence.DEAD;
        Entity.RemovalReason reason = known.getRemovalReason();
        if (reason == Entity.RemovalReason.KILLED) return EntityPresence.DEAD;
        if (reason == Entity.RemovalReason.DISCARDED) return EntityPresence.CONFIRMED_GONE;
        return EntityPresence.UNKNOWN;
    }

    public Entity observableEntity(EntityMaid maid, ServerLevel level, UUID entityId) {
        return entityPresence(maid, level, entityId) == EntityPresence.OBSERVABLE
                ? level.getEntity(entityId) : null;
    }

    private void remember(Entity entity) {
        trackedEntities.put(entity.getUUID(), new WeakReference<>(entity));
    }

    private JsonObject entityPresenceSnapshot(EntityMaid maid, ServerLevel level) {
        JsonObject result = new JsonObject();
        for (UUID entityId : List.copyOf(trackedEntities.keySet())) {
            result.addProperty(entityId.toString(), entityPresence(maid, level, entityId).name());
        }
        return result;
    }

    public JsonObject inspectNearbyEntities(
            EntityMaid maid,
            ServerLevel level,
            double requestedRadius,
            String requestedCategory,
            boolean hostileOnly,
            boolean targetingOnly
    ) {
        double radius = Math.max(1, Math.min(BridgeConfig.ENTITY_OBSERVE_RADIUS.get(), requestedRadius));
        String categoryFilter = requestedCategory == null ? "ANY" : requestedCategory.toUpperCase(Locale.ROOT);
        List<Entity> candidates = level.getEntities(maid, maid.getBoundingBox().inflate(radius), Entity::isAlive);
        candidates.sort(Comparator.comparingDouble(maid::distanceToSqr));
        JsonArray rows = new JsonArray();
        Set<String> hostileDirections = new HashSet<>();
        JsonObject nearest = null;
        for (Entity entity : candidates) {
            remember(entity);
            if (rows.size() >= 96 || !maid.hasLineOfSight(entity)) continue;
            boolean hostile = threats.hostile(entity, maid);
            boolean targeting = entity instanceof Mob mob && mob.getTarget() == maid;
            String category = category(entity, hostile);
            if (!"ANY".equals(categoryFilter) && !categoryFilter.equals(category)) continue;
            if (hostileOnly && !hostile) continue;
            if (targetingOnly && !targeting) continue;
            JsonObject row = new JsonObject();
            appendEntity(row, maid, entity);
            rows.add(row);
            if (nearest == null) nearest = row.deepCopy();
            if (hostile) hostileDirections.add(row.get("direction").getAsString());
        }
        JsonObject result = new JsonObject();
        result.addProperty("count", rows.size());
        result.add("entities", rows);
        if (nearest != null) result.add("nearest", nearest);
        result.addProperty("multiple_threat_directions", hostileDirections.size() >= 2);
        JsonArray directions = new JsonArray();
        hostileDirections.stream().sorted().forEach(directions::add);
        result.add("threat_directions", directions);
        return result;
    }

    public JsonObject inspectBlock(EntityMaid maid, ServerLevel level, BlockPos target) {
        JsonObject result = new JsonObject();
        int radius = BridgeConfig.VISIBLE_BLOCK_RADIUS.get();
        if (!level.hasChunkAt(target) || maid.blockPosition().distSqr(target) > radius * radius
                || !visibility.canSee(maid, level, target)) {
            result.addProperty("exists", false);
            result.addProperty("visible", false);
            return result;
        }
        BlockState state = level.getBlockState(target);
        result.addProperty("exists", !state.isAir());
        result.addProperty("visible", true);
        result.addProperty("block_id", BuiltInRegistries.BLOCK.getKey(state.getBlock()).toString());
        result.add("block_state", blockState(state));
        result.addProperty("air", state.isAir());
        result.addProperty("replaceable", state.canBeReplaced());
        result.addProperty("interactable", isInteractable(level, target, state));
        return result;
    }

    public JsonObject inspectLocalSpace(EntityMaid maid, ServerLevel level, BlockPos requestedTarget) {
        JsonObject result = new JsonObject();
        double radians = Math.toRadians(maid.getYRot());
        int forwardX = Mth.floor(maid.getX() - Math.sin(radians));
        int forwardZ = Mth.floor(maid.getZ() + Math.cos(radians));
        BlockPos front = new BlockPos(forwardX, Mth.floor(maid.getY()), forwardZ);
        BlockPos below = maid.blockPosition().below();
        boolean frontBlocked = !level.getBlockState(front).getCollisionShape(level, front).isEmpty()
                || !level.getBlockState(front.above()).getCollisionShape(level, front.above()).isEmpty();
        boolean supported = level.getBlockState(below).isFaceSturdy(level, below, Direction.UP);
        result.addProperty("front_blocked", frontBlocked);
        result.addProperty("support_below", supported);
        result.addProperty("in_water", maid.isInWater());
        result.addProperty("on_fire", maid.isOnFire());
        result.addProperty("fall_risk", obviousFallRisk(level, front));
        if (requestedTarget != null) {
            int radius = BridgeConfig.VISIBLE_BLOCK_RADIUS.get();
            boolean observable = level.hasChunkAt(requestedTarget)
                    && maid.blockPosition().distSqr(requestedTarget) <= radius * radius
                    && visibility.canSee(maid, level, requestedTarget);
            boolean clear = observable
                    && level.getBlockState(requestedTarget).canBeReplaced()
                    && level.getBlockState(requestedTarget.above()).canBeReplaced()
                    && level.noCollision(new AABB(requestedTarget))
                    && level.noCollision(new AABB(requestedTarget.above()));
            result.addProperty("target_observable", observable);
            result.addProperty("target_space_free", clear);
        }
        return result;
    }

    private JsonArray nearbyEntities(EntityMaid maid, ServerLevel level) {
        return inspectNearbyEntities(
                maid, level, BridgeConfig.ENTITY_OBSERVE_RADIUS.get(), "ANY", false, false
        ).getAsJsonArray("entities");
    }

    private void appendEntity(JsonObject item, EntityMaid maid, Entity entity) {
        remember(entity);
        item.addProperty("uuid", entity.getUUID().toString());
        item.addProperty("presence", EntityPresence.OBSERVABLE.name());
        item.addProperty("exists", true);
        item.addProperty("visible", true);
        item.addProperty("type", BuiltInRegistries.ENTITY_TYPE.getKey(entity.getType()).toString());
        boolean hostile = threats.hostile(entity, maid);
        item.addProperty("category", category(entity, hostile));
        double dx = entity.getX() - maid.getX();
        double dy = entity.getY() - maid.getY();
        double dz = entity.getZ() - maid.getZ();
        double distance = Math.sqrt(maid.distanceToSqr(entity));
        item.addProperty("distance", Math.round(distance * 10.0) / 10.0);
        JsonObject relative = new JsonObject();
        relative.addProperty("dx", dx);
        relative.addProperty("dy", dy);
        relative.addProperty("dz", dz);
        item.add("relative", relative);
        item.addProperty("direction", relativeDirection(maid, dx, dz));
        if (entity instanceof LivingEntity living) {
            item.addProperty("alive", living.isAlive());
            item.addProperty("health", living.getHealth());
        } else {
            item.addProperty("alive", entity.isAlive());
        }
        item.addProperty("moving", entity.getDeltaMovement().horizontalDistanceSqr() > 0.0004);
        item.addProperty("line_of_sight", maid.hasLineOfSight(entity));
        item.addProperty("targeting_maid", entity instanceof Mob mob && mob.getTarget() == maid);
        item.addProperty("hostile", hostile);
    }

    private String category(Entity entity, boolean hostile) {
        if (hostile) return "HOSTILE";
        if (entity instanceof Player) return "PLAYER";
        if (entity instanceof ItemEntity) return "ITEM";
        if (entity instanceof LivingEntity) return "PASSIVE";
        return "OTHER";
    }

    private static String relativeDirection(EntityMaid maid, double dx, double dz) {
        double absoluteYaw = Math.toDegrees(Math.atan2(-dx, dz));
        float relative = Mth.wrapDegrees((float) (absoluteYaw - maid.getYRot()));
        if (relative >= -45 && relative < 45) return "FRONT";
        if (relative >= 45 && relative < 135) return "LEFT";
        if (relative <= -45 && relative > -135) return "RIGHT";
        return "BACK";
    }

    private boolean obviousFallRisk(ServerLevel level, BlockPos position) {
        for (int depth = 0; depth <= 3; depth++) {
            BlockPos check = position.below(depth);
            if (level.getBlockState(check).isFaceSturdy(level, check, Direction.UP)) return false;
        }
        return true;
    }

    private boolean isInteractable(ServerLevel level, BlockPos target, BlockState state) {
        return state.getMenuProvider(level, target) != null
                || state.getBlock() instanceof DoorBlock
                || state.getBlock() instanceof TrapDoorBlock
                || state.getBlock() instanceof FenceGateBlock
                || state.getBlock() instanceof ButtonBlock
                || state.getBlock() instanceof LeverBlock
                || state.getBlock() instanceof BasePressurePlateBlock;
    }

    private JsonArray visibleBlocks(EntityMaid maid, ServerLevel level) {
        int radius = BridgeConfig.VISIBLE_BLOCK_RADIUS.get();
        boolean allowHidden = BridgeConfig.ALLOW_HIDDEN_BLOCK_SCAN.get();
        BlockPos center = maid.blockPosition();
        JsonArray array = new JsonArray();
        int inspected = 0;
        outer: for (int shell = 0; shell <= radius; shell++) {
            for (int dy = -shell; dy <= shell; dy++) {
                for (int dx = -shell; dx <= shell; dx++) {
                    for (int dz = -shell; dz <= shell; dz++) {
                        if (Math.max(Math.max(Math.abs(dx), Math.abs(dy)), Math.abs(dz)) != shell) continue;
                        if (++inspected > 18000 || array.size() >= 512) break outer;
                        BlockPos pos = center.offset(dx, dy, dz);
                        BlockState state = level.getBlockState(pos);
                        if (state.isAir()) continue;
                        boolean exposed = visibility.isExposed(level, pos);
                        if (!allowHidden && (!exposed || !visibility.canSee(maid, level, pos))) continue;
                        JsonObject item = new JsonObject();
                        item.addProperty("id", BuiltInRegistries.BLOCK.getKey(state.getBlock()).toString());
                        item.addProperty("x", pos.getX());
                        item.addProperty("y", pos.getY());
                        item.addProperty("z", pos.getZ());
                        item.addProperty("exposed", exposed);
                        item.add("state", blockState(state));
                        array.add(item);
                    }
                }
            }
        }
        return array;
    }

    private static JsonObject blockState(BlockState state) {
        JsonObject properties = new JsonObject();
        for (Property<?> property : state.getProperties()) {
            properties.addProperty(property.getName(), valueName(state, property));
        }
        return properties;
    }

    private static <T extends Comparable<T>> String valueName(BlockState state, Property<T> property) {
        return property.getName(state.getValue(property));
    }
}
