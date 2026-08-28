package com.maidaibridge.action.impl;

import com.google.gson.JsonObject;
import com.maidaibridge.BridgeConfig;
import com.maidaibridge.action.AbstractMaidAction;
import com.maidaibridge.action.ActionContext;
import com.maidaibridge.observe.MaidObservationService;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.item.Item;

import java.util.Locale;
import java.util.Set;
import java.util.UUID;
import java.util.function.Predicate;

/** Polls only a fixed allow-list of observable world conditions. */
public final class WaitUntilAction extends AbstractMaidAction {
    private static final Set<String> ALLOWED = Set.of(
            "ENTITY_EXISTS", "ENTITY_GONE", "ENTITY_DISTANCE_AT_MOST", "ENTITY_DISTANCE_AT_LEAST",
            "BLOCK_ID_EQUALS", "BLOCK_AIR", "POSITION_REACHED", "ACTION_COMPLETE", "HAS_ITEM",
            "HEALTH_AT_LEAST", "HEALTH_BELOW"
    );

    private final String conditionType;
    private final JsonObject args;
    private final String failureCode;
    private final Predicate<String> actionComplete;
    private final MaidObservationService observations;

    public WaitUntilAction(
            String requestId,
            int timeout,
            JsonObject condition,
            String failureCode,
            MaidObservationService observations,
            Predicate<String> actionComplete
    ) {
        super(requestId, "wait_until", timeout);
        this.conditionType = string(condition, "type", "").toUpperCase(Locale.ROOT);
        if (!ALLOWED.contains(this.conditionType)) throw new IllegalArgumentException("INVALID_CONDITION");
        this.args = condition.has("args") && condition.get("args").isJsonObject()
                ? condition.getAsJsonObject("args").deepCopy() : new JsonObject();
        this.failureCode = failureCode == null || failureCode.isBlank() ? "CONDITION_TIMEOUT" : failureCode;
        this.observations = observations;
        this.actionComplete = actionComplete;
    }

    @Override
    protected void onStart(ActionContext context) {
        try {
            switch (conditionType) {
                case "ENTITY_EXISTS", "ENTITY_GONE" -> requiredUuid();
                case "ENTITY_DISTANCE_AT_MOST", "ENTITY_DISTANCE_AT_LEAST" -> {
                    requiredUuid();
                    number(args, "distance");
                }
                case "BLOCK_ID_EQUALS" -> {
                    requiredPosition();
                    if (ResourceLocation.tryParse(string(args, "block_id", "")) == null) {
                        throw new IllegalArgumentException("INVALID_CONDITION");
                    }
                }
                case "BLOCK_AIR", "POSITION_REACHED" -> requiredPosition();
                case "ACTION_COMPLETE" -> {
                    if (string(args, "request_id", "").isBlank()) throw new IllegalArgumentException("INVALID_CONDITION");
                }
                case "HAS_ITEM" -> {
                    if (ResourceLocation.tryParse(string(args, "item_id", "")) == null) {
                        throw new IllegalArgumentException("INVALID_CONDITION");
                    }
                }
                case "HEALTH_AT_LEAST", "HEALTH_BELOW" -> number(args, "value");
                default -> throw new IllegalArgumentException("INVALID_CONDITION");
            }
        } catch (RuntimeException exception) {
            fail("INVALID_CONDITION");
        }
    }

    @Override
    protected void onTick(ActionContext context) {
        boolean met = evaluate(context);
        data.addProperty("condition_type", conditionType);
        data.addProperty("condition_met", met);
        data.addProperty("elapsed_ticks", context.gameTick() - startTick);
        if (met) succeed("CONDITION_MET");
    }

    private boolean evaluate(ActionContext context) {
        return switch (conditionType) {
            case "ENTITY_EXISTS" -> entityExists(context, requiredUuid());
            case "ENTITY_GONE" -> entityGone(context, requiredUuid());
            case "ENTITY_DISTANCE_AT_MOST" -> finiteDistanceAtMost(context, requiredUuid(), number(args, "distance"));
            case "ENTITY_DISTANCE_AT_LEAST" -> finiteDistanceAtLeast(context, requiredUuid(), number(args, "distance"));
            case "BLOCK_ID_EQUALS" -> blockMatches(context, requiredPosition(), string(args, "block_id", ""));
            case "BLOCK_AIR" -> blockAir(context, requiredPosition());
            case "POSITION_REACHED" -> context.maid().position().distanceTo(
                    net.minecraft.world.phys.Vec3.atCenterOf(requiredPosition())
            ) <= number(args, "radius", 1.5);
            case "ACTION_COMPLETE" -> actionComplete.test(string(args, "request_id", ""));
            case "HAS_ITEM" -> hasItem(context, string(args, "item_id", ""), integer(args, "count", 1));
            case "HEALTH_AT_LEAST" -> context.maid().getHealth() >= number(args, "value");
            case "HEALTH_BELOW" -> context.maid().getHealth() < number(args, "value");
            default -> false;
        };
    }

    private boolean entityExists(ActionContext context, UUID id) {
        MaidObservationService.EntityPresence presence = observations.entityPresence(
                context.maid(), context.level(), id
        );
        return presence == MaidObservationService.EntityPresence.OBSERVABLE
                || presence == MaidObservationService.EntityPresence.PRESENT_NOT_OBSERVABLE;
    }

    private boolean entityGone(ActionContext context, UUID id) {
        MaidObservationService.EntityPresence presence = observations.entityPresence(
                context.maid(), context.level(), id
        );
        return presence == MaidObservationService.EntityPresence.DEAD
                || presence == MaidObservationService.EntityPresence.CONFIRMED_GONE;
    }

    private double entityDistance(ActionContext context, UUID id) {
        Entity entity = observations.observableEntity(context.maid(), context.level(), id);
        return entity == null ? Double.NaN : context.maid().distanceTo(entity);
    }

    private boolean finiteDistanceAtMost(ActionContext context, UUID id, double distance) {
        double actual = entityDistance(context, id);
        return Double.isFinite(actual) && actual <= distance;
    }

    private boolean finiteDistanceAtLeast(ActionContext context, UUID id, double distance) {
        double actual = entityDistance(context, id);
        return Double.isFinite(actual) && actual >= distance;
    }

    private boolean blockMatches(ActionContext context, BlockPos target, String expected) {
        if (!observableBlock(context, target)) return false;
        ResourceLocation id = BuiltInRegistries.BLOCK.getKey(context.level().getBlockState(target).getBlock());
        return id != null && id.toString().equals(expected);
    }

    private boolean blockAir(ActionContext context, BlockPos target) {
        return observableBlock(context, target) && context.level().getBlockState(target).isAir();
    }

    private boolean observableBlock(ActionContext context, BlockPos target) {
        int radius = BridgeConfig.VISIBLE_BLOCK_RADIUS.get();
        return context.level().hasChunkAt(target)
                && context.maid().blockPosition().distSqr(target) <= radius * radius
                && context.visibility().canSee(context.maid(), context.level(), target);
    }

    private boolean hasItem(ActionContext context, String itemId, int count) {
        ResourceLocation key = ResourceLocation.tryParse(itemId);
        if (key == null || !BuiltInRegistries.ITEM.containsKey(key)) return false;
        Item item = BuiltInRegistries.ITEM.get(key);
        return context.inventory().count(context.maid(), item) >= Math.max(1, count);
    }

    private UUID requiredUuid() {
        try {
            return UUID.fromString(string(args, "uuid", ""));
        } catch (Exception exception) {
            throw new IllegalArgumentException("INVALID_TARGET_UUID");
        }
    }

    private BlockPos requiredPosition() {
        JsonObject source = args.has("position") && args.get("position").isJsonObject()
                ? args.getAsJsonObject("position") : args;
        return new BlockPos(
                (int) Math.floor(number(source, "x")),
                (int) Math.floor(number(source, "y")),
                (int) Math.floor(number(source, "z"))
        );
    }

    @Override
    protected String timeoutCode() {
        return failureCode;
    }

    private static String string(JsonObject object, String key, String fallback) {
        try { return object.has(key) ? object.get(key).getAsString() : fallback; }
        catch (Exception ignored) { return fallback; }
    }

    private static double number(JsonObject object, String key) {
        if (!object.has(key)) throw new IllegalArgumentException("INVALID_CONDITION");
        return object.get(key).getAsDouble();
    }

    private static double number(JsonObject object, String key, double fallback) {
        try { return object.has(key) ? object.get(key).getAsDouble() : fallback; }
        catch (Exception ignored) { return fallback; }
    }

    private static int integer(JsonObject object, String key, int fallback) {
        try { return object.has(key) ? object.get(key).getAsInt() : fallback; }
        catch (Exception ignored) { return fallback; }
    }
}
