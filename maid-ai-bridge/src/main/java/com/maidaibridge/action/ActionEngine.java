package com.maidaibridge.action;

import com.github.tartaricacid.touhoulittlemaid.entity.passive.EntityMaid;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.maidaibridge.BridgeConfig;
import com.maidaibridge.action.impl.*;
import com.maidaibridge.controller.MaidBridgeEventPublisher;
import com.maidaibridge.controller.MotionArbiter;
import com.maidaibridge.craft.MaidCraftService;
import com.maidaibridge.inventory.MaidInventoryService;
import com.maidaibridge.observe.MaidObservationService;
import com.maidaibridge.observe.VisibilityService;
import com.maidaibridge.smelt.MaidSmeltService;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.tags.TagKey;
import net.minecraft.world.Container;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.world.item.BlockItem;
import net.minecraft.world.item.Item;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.phys.AABB;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;
import java.util.function.BiConsumer;
import java.util.function.Consumer;

/** Server-thread action dispatcher, replay cache and lifecycle owner. */
public final class ActionEngine {
    private static final int MAX_REPLAY_RESULTS = 512;

    private final MotionArbiter motion;
    private final MaidInventoryService inventory;
    private final VisibilityService visibility;
    private final MaidObservationService observations;
    private final MaidCraftService craft;
    private final MaidSmeltService smelt;
    private final BiConsumer<String, JsonObject> sender;
    private final MaidBridgeEventPublisher events;
    private final Consumer<MaidAction> checkpointWriter;
    private final Runnable checkpointClearer;
    private final LinkedHashMap<String, ActionResult> replayResults = new LinkedHashMap<>();

    private MaidAction active;
    private String reflexState = "NONE";
    private ActionContext lastContext;

    public ActionEngine(
            MotionArbiter motion,
            MaidInventoryService inventory,
            MaidObservationService observations,
            BiConsumer<String, JsonObject> sender,
            MaidBridgeEventPublisher events,
            Consumer<MaidAction> checkpointWriter,
            Runnable checkpointClearer
    ) {
        this.motion = motion;
        this.inventory = inventory;
        this.visibility = new VisibilityService();
        this.observations = observations;
        this.craft = new MaidCraftService(inventory);
        this.smelt = new MaidSmeltService(inventory);
        this.sender = sender;
        this.events = events;
        this.checkpointWriter = checkpointWriter;
        this.checkpointClearer = checkpointClearer;
        this.motion.setPreemptionListener(this::motionPreempted);
    }

    public void submit(JsonObject payload, EntityMaid maid, ServerLevel level, long tick) {
        MaidActionSafety.requireServerThread(context(maid, level, tick));
        String requestId = str(payload, "request_id", UUID.randomUUID().toString());
        String type = str(payload, "action", "");
        JsonObject args = payload.has("args") && payload.get("args").isJsonObject()
                ? payload.getAsJsonObject("args") : new JsonObject();
        int timeout = integer(payload, "timeout_ticks", BridgeConfig.ACTION_TIMEOUT_TICKS.get());
        ActionContext context = context(maid, level, tick);
        lastContext = context;

        ActionResult replay = replayResults.get(requestId);
        if (replay != null) {
            sender.accept("ACTION_RESULT", replay.toPayload());
            return;
        }
        if (active != null && active.requestId().equals(requestId) && !active.isTerminal()) {
            sendAck(requestId, active.id().toString());
            return;
        }
        if (type.isBlank()) {
            sendFailure(requestId, "INVALID_ACTION", "missing action", context, true);
            return;
        }
        if (isObservation(type)) {
            sendAck(requestId, "observation");
            ActionResult result = immediateObservation(requestId, type, args, context);
            sendResult(result, context, true, false);
            return;
        }
        if (type.equals("cancel_action") || type.equals("stop")) {
            if (active != null && !active.isTerminal()) {
                active.cancel(context, CancelReason.USER);
                finishActive(context);
            }
            motion.forceStop(maid);
            MaidAction stop = new StopAction(requestId);
            sendAck(requestId, stop.id().toString());
            stop.tick(context);
            sendResult(stop.result(), context, true, true);
            return;
        }
        if (active != null && !active.isTerminal()) {
            sendFailure(requestId, "BUSY", "another body/world action is active", context, true);
            return;
        }

        try {
            active = create(requestId, type, args, timeout);
            sendAck(requestId, active.id().toString());
            events.actionStarted(maid, level, tick, requestId, active.id().toString(), type);
            active.start(context);
            if (active.isTerminal()) {
                finishActive(context);
            } else {
                checkpointWriter.accept(active);
            }
        } catch (Exception exception) {
            active = null;
            String message = exception.getMessage() == null
                    ? exception.getClass().getSimpleName() : exception.getMessage();
            String code = message.matches("[A-Z0-9_]+") ? message : "INVALID_ARGS";
            sendFailure(requestId, code, message, context, true);
        }
    }

    public void tick(EntityMaid maid, ServerLevel level, long tick) {
        if (active == null) return;
        ActionContext context = context(maid, level, tick);
        lastContext = context;
        if (!maid.isAlive()) active.cancel(context, CancelReason.MAID_DEAD);
        else active.tick(context);
        if (active != null && active.isTerminal()) finishActive(context);
    }

    public void cancel(EntityMaid maid, ServerLevel level, long tick, CancelReason reason) {
        ActionContext context = context(maid, level, tick);
        lastContext = context;
        if (active != null && !active.isTerminal()) {
            active.cancel(context, reason);
            finishActive(context);
        }
        motion.forceStop(maid);
    }

    public void preempt(EntityMaid maid, ServerLevel level, long tick, String reflex) {
        reflexState = reflex;
        ActionContext context = context(maid, level, tick);
        lastContext = context;
        if (active != null && !active.isTerminal()) {
            active.cancel(context, CancelReason.PREEMPTED_BY_REFLEX);
            finishActive(context);
        }
    }

    public void clearReflex() { reflexState = "NONE"; }
    public String reflexState() { return reflexState; }
    public JsonObject currentSummary() { return active == null ? new JsonObject() : active.summary(); }

    private void motionPreempted(com.maidaibridge.controller.MotionLease lease) {
        if (active == null || active.isTerminal() || lastContext == null) return;
        if (!active.id().equals(lease.actionId())) return;
        active.cancel(lastContext, CancelReason.PREEMPTED_BY_REFLEX);
        finishActive(lastContext);
    }

    private void finishActive(ActionContext context) {
        if (active == null) return;
        ActionResult result = active.result();
        active = null;
        checkpointClearer.run();
        sendResult(result, context, true, true);
    }

    private ActionContext context(EntityMaid maid, ServerLevel level, long tick) {
        return new ActionContext(maid, level, tick, motion, inventory, visibility);
    }

    private MaidAction create(String requestId, String type, JsonObject args, int timeout) {
        return switch (type) {
            case "move_to" -> new MoveToAction(requestId, type, timeout,
                    number(args, "x"), number(args, "y"), number(args, "z"),
                    number(args, "range", 1.5), number(args, "speed", .8));
            case "look_at" -> new LookAtAction(requestId, timeout,
                    number(args, "x"), number(args, "y"), number(args, "z"));
            case "face_position" -> new FacePositionAction(requestId,
                    Math.min(timeout, integer(args, "max_duration_ticks", 40)),
                    number(args, "x"), number(args, "y"), number(args, "z"),
                    number(args, "tolerance_degrees", 5));
            case "face_entity" -> new FaceEntityAction(requestId, timeout,
                    uuid(args, "uuid", "entity_uuid"), bool(args, "track", false),
                    integer(args, "max_duration_ticks", 100));
            case "follow_entity" -> new FollowEntityAction(requestId, timeout,
                    uuid(args, "uuid", "entity_uuid"), number(args, "range", 2));
            case "move_forward" -> new RelativeMoveAction(requestId, type,
                    Math.min(timeout, integer(args, "max_duration_ticks", 100)),
                    number(args, "max_distance", 3), str(args, "stop_condition", "ANY"), 0, false);
            case "move_backward" -> new RelativeMoveAction(requestId, type,
                    Math.min(timeout, integer(args, "max_duration_ticks", 100)),
                    number(args, "max_distance", 3), str(args, "stop_condition", "ANY"), 2, false);
            case "strafe_left" -> new RelativeMoveAction(requestId, type,
                    Math.min(timeout, integer(args, "max_duration_ticks", 100)),
                    number(args, "max_distance", 3), str(args, "stop_condition", "ANY"), 1, false);
            case "strafe_right" -> new RelativeMoveAction(requestId, type,
                    Math.min(timeout, integer(args, "max_duration_ticks", 100)),
                    number(args, "max_distance", 3), str(args, "stop_condition", "ANY"), -1, false);
            case "short_sprint" -> new RelativeMoveAction(requestId, type,
                    Math.min(timeout, integer(args, "max_duration_ticks", 100)),
                    number(args, "max_distance", 5), str(args, "stop_condition", "ANY"), 0, true);
            case "approach_entity" -> new EntityDistanceAction(requestId, type, timeout,
                    uuid(args, "uuid", "entity_uuid"), EntityDistanceAction.Mode.APPROACH,
                    number(args, "target_distance", 2), number(args, "target_distance", 2),
                    integer(args, "max_duration_ticks", 200));
            case "move_away_from_entity" -> new EntityDistanceAction(requestId, type, timeout,
                    uuid(args, "uuid", "entity_uuid"), EntityDistanceAction.Mode.AWAY,
                    number(args, "target_distance", 10), number(args, "target_distance", 10),
                    integer(args, "max_duration_ticks", 200));
            case "maintain_distance" -> new EntityDistanceAction(requestId, type, timeout,
                    uuid(args, "uuid", "entity_uuid"), EntityDistanceAction.Mode.MAINTAIN,
                    number(args, "min_distance"), number(args, "max_distance"),
                    integer(args, "timeout_ticks", 200));
            case "jump" -> new BodyControlAction(requestId, type,
                    Math.min(timeout, integer(args, "max_duration_ticks", 20)), BodyControlAction.Kind.JUMP);
            case "sneak_on" -> new BodyControlAction(requestId, type,
                    Math.min(timeout, integer(args, "max_duration_ticks", 20)), BodyControlAction.Kind.SNEAK_ON);
            case "sneak_off" -> new BodyControlAction(requestId, type,
                    Math.min(timeout, integer(args, "max_duration_ticks", 20)), BodyControlAction.Kind.SNEAK_OFF);
            case "break_block" -> new BreakBlockAction(requestId, timeout, position(args));
            case "place_block" -> new PlaceBlockAction(requestId, timeout, position(args),
                    item(str(args, "item_id", str(args, "block_item_id", ""))),
                    direction(str(args, "face", "UP")));
            case "pickup_nearby" -> new PickupNearbyAction(requestId, timeout,
                    number(args, "radius", 12), optionalItem(str(args, "item_id", "")));
            case "equip" -> new EquipAction(requestId, timeout,
                    item(str(args, "item_id", "")), slot(str(args, "slot", "MAINHAND")));
            case "select_item", "move_item_to_main_hand" -> new EquipAction(requestId, type, timeout,
                    item(str(args, "item_id", "")), EquipmentSlot.MAINHAND);
            case "move_item_to_off_hand" -> new EquipAction(requestId, type, timeout,
                    item(str(args, "item_id", "")), EquipmentSlot.OFFHAND);
            case "eat" -> new EatAction(requestId, timeout, optionalItem(str(args, "item_id", "")));
            case "use_item" -> new UseItemAction(requestId, timeout, item(str(args, "item_id", "")));
            case "use_main_hand" -> new HandUseAction(requestId, type,
                    Math.min(timeout, integer(args, "max_duration_ticks", 80)), net.minecraft.world.InteractionHand.MAIN_HAND);
            case "use_off_hand" -> new HandUseAction(requestId, type,
                    Math.min(timeout, integer(args, "max_duration_ticks", 80)), net.minecraft.world.InteractionHand.OFF_HAND);
            case "use_item_on_block" -> new UseItemOnBlockAction(requestId, timeout,
                    position(args), direction(str(args, "face", "UP")), hand(str(args, "hand", "MAIN_HAND")));
            case "use_block" -> new UseBlockAction(requestId, type, timeout, position(args),
                    hand(str(args, "hand", "MAIN_HAND")), direction(str(args, "face", "UP")));
            case "interact_block" -> new UseBlockAction(requestId, type, timeout, position(args),
                    hand(str(args, "hand", "MAIN_HAND")), direction(str(args, "face", "UP")));
            case "attack_entity" -> new AttackEntityAction(requestId,
                    Math.min(timeout, integer(args, "max_duration_ticks", 200)),
                    uuid(args, "uuid", "entity_uuid"), integer(args, "max_attack_count", 3),
                    integer(args, "max_duration_ticks", 200), str(args, "stop_condition", "ANY"));
            case "interact_entity" -> new InteractEntityAction(requestId, timeout,
                    uuid(args, "uuid", "entity_uuid"), hand(str(args, "hand", "MAIN_HAND")));
            case "retreat_from" -> new RetreatAction(requestId, timeout,
                    uuid(args, "uuid", "entity_uuid"), number(args, "distance", 12));
            case "hold_position" -> new HoldPositionAction(requestId, timeout,
                    integer(args, "duration_ticks", 100));
            case "wait" -> new WaitAction(requestId,
                    Math.min(timeout, integer(args, "timeout_ticks", timeout)),
                    integer(args, "duration_ticks", 1));
            case "wait_until" -> new WaitUntilAction(requestId,
                    Math.min(timeout, integer(args, "timeout_ticks", timeout)),
                    requiredObject(args, "condition"), str(args, "failure_code", "CONDITION_TIMEOUT"),
                    observations,
                    completedRequestId -> replayResults.containsKey(completedRequestId));
            case "craft" -> new CraftAction(requestId, timeout, craft,
                    item(str(args, "item_id", "")), integer(args, "count", 1));
            case "smelt" -> new SmeltAction(requestId, timeout, smelt, position(args),
                    item(str(args, "input_item_id", "")), item(str(args, "output_item_id", "")),
                    integer(args, "count", 1));
            case "transfer_container" -> new TransferContainerAction(requestId, timeout,
                    position(args), item(str(args, "item_id", "")), integer(args, "count", 1),
                    !str(args, "direction", "TO_CONTAINER").equalsIgnoreCase("FROM_CONTAINER"),
                    bool(args, "allow_partial", true));
            case "open_container" -> new OpenContainerAction(requestId, timeout, position(args));
            case "take_from_container" -> new TransferContainerAction(requestId, type, timeout,
                    position(args), item(str(args, "item_id", "")), integer(args, "count", 1),
                    false, bool(args, "allow_partial", true));
            case "put_into_container" -> new TransferContainerAction(requestId, type, timeout,
                    position(args), item(str(args, "item_id", "")), integer(args, "count", 1),
                    true, bool(args, "allow_partial", true));
            case "dig_region" -> new DigRegionAction(requestId, timeout,
                    position(args, "min"), position(args, "max"));
            case "place_region" -> new PlaceRegionAction(requestId, timeout,
                    position(args, "min"), position(args, "max"), item(str(args, "item_id", "")));
            case "build_chunk" -> new BuildChunkAction(requestId, timeout,
                    buildPlacements(args), bool(args, "allow_partial", true));
            default -> throw new IllegalArgumentException("UNKNOWN_ACTION");
        };
    }

    private boolean isObservation(String type) {
        return switch (type) {
            case "get_status", "get_inventory", "inspect_area", "find_visible_block", "find_place_position",
                    "find_entity", "inspect_entity", "inspect_nearby_entities", "inspect_block",
                    "inspect_local_space", "inspect_container", "has_item", "get_action_status" -> true;
            default -> false;
        };
    }

    private ActionResult immediateObservation(
            String requestId, String type, JsonObject args, ActionContext context
    ) {
        JsonObject data = new JsonObject();
        String failureCode = null;
        String failureMessage = "";
        switch (type) {
            case "get_status" -> data = observations.snapshot(context.maid(), context.level(), this);
            case "inspect_area" -> {
                data = observations.snapshot(context.maid(), context.level(), this);
                int count = data.getAsJsonArray("visible_blocks").size()
                        + data.getAsJsonArray("nearby_entities").size();
                data.addProperty("observations", count);
            }
            case "get_inventory" -> data.add("inventory", inventory.snapshot(context.maid()));
            case "get_action_status" -> data.add("current_action", currentSummary());
            case "inspect_entity" -> data = observations.inspectEntity(
                    context.maid(), context.level(), uuid(args, "uuid", "entity_uuid")
            );
            case "inspect_nearby_entities" -> data = observations.inspectNearbyEntities(
                    context.maid(), context.level(), number(args, "radius", 16),
                    str(args, "category", "ANY"), bool(args, "hostile_only", false),
                    bool(args, "targeting_maid_only", false)
            );
            case "inspect_block" -> data = observations.inspectBlock(
                    context.maid(), context.level(), position(args)
            );
            case "inspect_local_space" -> {
                BlockPos target = args.has("target") && args.get("target").isJsonObject()
                        ? position(args.getAsJsonObject("target")) : null;
                data = observations.inspectLocalSpace(context.maid(), context.level(), target);
            }
            case "has_item" -> {
                Item query = item(str(args, "item_id", ""));
                int required = Math.max(1, integer(args, "count", 1));
                int available = inventory.count(context.maid(), query);
                data.addProperty("item_id", BuiltInRegistries.ITEM.getKey(query).toString());
                data.addProperty("required", required);
                data.addProperty("available", available);
                data.addProperty("has_item", available >= required);
            }
            case "find_visible_block" -> {
                JsonObject snapshot = observations.snapshot(context.maid(), context.level(), this);
                String query = str(args, "query", "");
                int limit = Math.max(1, Math.min(64, integer(args, "limit", 16)));
                JsonArray matches = new JsonArray();
                for (JsonElement element : snapshot.getAsJsonArray("visible_blocks")) {
                    JsonObject row = element.getAsJsonObject();
                    if (matchesBlockQuery(row.get("id").getAsString(), query)) {
                        matches.add(row);
                        if (matches.size() >= limit) break;
                    }
                }
                if (matches.isEmpty()) {
                    failureCode = "TARGET_NOT_VISIBLE";
                    failureMessage = "no visible block matched " + query;
                } else {
                    JsonObject first = matches.get(0).getAsJsonObject();
                    JsonObject position = new JsonObject();
                    position.addProperty("x", first.get("x").getAsInt());
                    position.addProperty("y", first.get("y").getAsInt());
                    position.addProperty("z", first.get("z").getAsInt());
                    data.add("position", position);
                    data.add("match", first);
                    data.add("matches", matches);
                }
            }
            case "find_place_position" -> {
                Item placementItem = item(str(args, "item_id", ""));
                if (!(placementItem instanceof BlockItem)) {
                    failureCode = "ITEM_IS_NOT_BLOCK";
                    failureMessage = "requested placement item is not a block";
                    break;
                }
                if (inventory.count(context.maid(), placementItem) < 1) {
                    failureCode = "NO_MATERIAL";
                    failureMessage = "placement item is not present in Maid inventory";
                    break;
                }
                int radius = Math.max(1, Math.min(8, integer(args, "radius", 4)));
                BlockPos center = context.maid().blockPosition();
                BlockPos best = null;
                double bestDistance = Double.MAX_VALUE;
                for (int dy = -1; dy <= 1; dy++) {
                    for (int dx = -radius; dx <= radius; dx++) {
                        for (int dz = -radius; dz <= radius; dz++) {
                            BlockPos candidate = center.offset(dx, dy, dz);
                            if (!context.level().hasChunkAt(candidate)) continue;
                            if (!context.level().getBlockState(candidate).canBeReplaced()) continue;
                            BlockPos support = candidate.below();
                            if (!context.level().getBlockState(support)
                                    .isFaceSturdy(context.level(), support, Direction.UP)) continue;
                            if (!visibility.canSee(context.maid(), context.level(), support)) continue;
                            if (new AABB(candidate).intersects(context.maid().getBoundingBox())) continue;
                            if (!context.level().noCollision(new AABB(candidate))) continue;
                            if (!context.maid().canPlaceBlock(candidate)) continue;
                            double distance = candidate.distSqr(center);
                            if (distance < bestDistance) {
                                best = candidate.immutable();
                                bestDistance = distance;
                            }
                        }
                    }
                }
                if (best == null) {
                    failureCode = "NO_PLACE_POSITION";
                    failureMessage = "no supported visible placement position was found";
                } else {
                    JsonObject position = new JsonObject();
                    position.addProperty("x", best.getX());
                    position.addProperty("y", best.getY());
                    position.addProperty("z", best.getZ());
                    data.add("position", position);
                    data.addProperty("support_x", best.getX());
                    data.addProperty("support_y", best.getY() - 1);
                    data.addProperty("support_z", best.getZ());
                }
            }
            case "find_entity" -> {
                JsonObject snapshot = observations.snapshot(context.maid(), context.level(), this);
                String query = str(args, "query", "").toLowerCase(Locale.ROOT);
                boolean hostileOnly = bool(args, "hostile_only", false);
                JsonArray matches = new JsonArray();
                for (JsonElement element : snapshot.getAsJsonArray("nearby_entities")) {
                    JsonObject row = element.getAsJsonObject();
                    boolean hostile = "HOSTILE".equals(row.get("category").getAsString());
                    if (hostileOnly && !hostile) continue;
                    if (row.get("type").getAsString().toLowerCase(Locale.ROOT).contains(query)
                            || row.get("category").getAsString().toLowerCase(Locale.ROOT).contains(query)) {
                        matches.add(row);
                    }
                }
                data.add("matches", matches);
            }
            case "inspect_container" -> {
                BlockPos target = position(args);
                String safety = MaidActionSafety.validateCloseInteraction(context, target, 3.5);
                if (!safety.isEmpty()) {
                    failureCode = safety;
                    failureMessage = "container interaction safety check failed";
                } else if (context.level().getBlockEntity(target) instanceof Container container) {
                    JsonArray slots = new JsonArray();
                    for (int index = 0; index < container.getContainerSize(); index++) {
                        var stack = container.getItem(index);
                        if (stack.isEmpty()) continue;
                        JsonObject row = new JsonObject();
                        row.addProperty("slot", index);
                        row.addProperty("id", BuiltInRegistries.ITEM.getKey(stack.getItem()).toString());
                        row.addProperty("count", stack.getCount());
                        slots.add(row);
                    }
                    data.add("slots", slots);
                } else {
                    failureCode = "NO_CONTAINER";
                    failureMessage = "target is not a container";
                }
            }
            default -> {
                failureCode = "UNKNOWN_OBSERVATION";
                failureMessage = type;
            }
        }
        if (failureCode != null) {
            data.addProperty("message", failureMessage);
            return new ActionResult(UUID.randomUUID(), requestId, ActionState.FAILED,
                    failureCode, data, new JsonObject());
        }
        return new ActionResult(UUID.randomUUID(), requestId, ActionState.SUCCESS,
                "OBSERVED", data, new JsonObject());
    }

    private void sendAck(String requestId, String actionId) {
        JsonObject payload = new JsonObject();
        payload.addProperty("request_id", requestId);
        payload.addProperty("action_id", actionId);
        sender.accept("ACTION_ACK", payload);
    }

    private void sendResult(
            ActionResult result, ActionContext context, boolean cache, boolean publishEvent
    ) {
        if (cache) {
            replayResults.put(result.requestId(), result);
            while (replayResults.size() > MAX_REPLAY_RESULTS) {
                String first = replayResults.keySet().iterator().next();
                replayResults.remove(first);
            }
        }
        JsonObject payload = result.toPayload();
        sender.accept("ACTION_RESULT", payload);
        if (publishEvent) events.actionFinished(
                context.maid(), context.level(), context.gameTick(), payload
        );
    }

    private void sendFailure(
            String requestId, String code, String message, ActionContext context, boolean cache
    ) {
        JsonObject data = new JsonObject();
        data.addProperty("message", message == null ? "" : message);
        sendResult(new ActionResult(UUID.randomUUID(), requestId, ActionState.FAILED,
                code, data, new JsonObject()), context, cache, true);
    }

    private static List<BuildChunkAction.Placement> buildPlacements(JsonObject args) {
        if (!args.has("placements") || !args.get("placements").isJsonArray()) {
            throw new IllegalArgumentException("MISSING_PLACEMENTS");
        }
        JsonArray array = args.getAsJsonArray("placements");
        if (array.isEmpty() || array.size() > 128) throw new IllegalArgumentException("INVALID_PLACEMENT_COUNT");
        List<BuildChunkAction.Placement> placements = new ArrayList<>();
        for (JsonElement element : array) {
            if (!element.isJsonObject()) throw new IllegalArgumentException("INVALID_PLACEMENT");
            JsonObject row = element.getAsJsonObject();
            int index = integer(row, "index", placements.size());
            BlockPos position = new BlockPos(
                    (int) Math.floor(number(row, "x")),
                    (int) Math.floor(number(row, "y")),
                    (int) Math.floor(number(row, "z"))
            );
            Item item = item(str(row, "item_id", ""));
            if (!(item instanceof net.minecraft.world.item.BlockItem)) {
                throw new IllegalArgumentException("ITEM_IS_NOT_BLOCK");
            }
            placements.add(new BuildChunkAction.Placement(
                    index, position, item, direction(str(row, "face", "UP"))
            ));
        }
        return placements;
    }

    private static boolean matchesBlockQuery(String id, String query) {
        if (query == null || query.isBlank()) return true;
        ResourceLocation blockId = ResourceLocation.tryParse(id);
        if (blockId == null) return false;
        if (query.startsWith("#")) {
            ResourceLocation tagId = ResourceLocation.tryParse(query.substring(1));
            if (tagId == null) return false;
            Block block = BuiltInRegistries.BLOCK.get(blockId);
            return block.defaultBlockState().is(TagKey.create(Registries.BLOCK, tagId));
        }
        return id.equals(query) || id.contains(query);
    }

    private static String str(JsonObject object, String key, String fallback) {
        try { return object.has(key) ? object.get(key).getAsString() : fallback; }
        catch (Exception ignored) { return fallback; }
    }

    private static double number(JsonObject object, String key) {
        if (!object.has(key)) throw new IllegalArgumentException("MISSING_" + key.toUpperCase(Locale.ROOT));
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

    private static boolean bool(JsonObject object, String key, boolean fallback) {
        try { return object.has(key) ? object.get(key).getAsBoolean() : fallback; }
        catch (Exception ignored) { return fallback; }
    }

    private static JsonObject requiredObject(JsonObject object, String key) {
        if (!object.has(key) || !object.get(key).isJsonObject()) {
            throw new IllegalArgumentException("INVALID_" + key.toUpperCase(Locale.ROOT));
        }
        return object.getAsJsonObject(key);
    }

    private static UUID uuid(JsonObject object, String primary, String alias) {
        String value = str(object, primary, str(object, alias, ""));
        if (value.isBlank()) throw new IllegalArgumentException("MISSING_TARGET_UUID");
        try { return UUID.fromString(value); }
        catch (IllegalArgumentException exception) { throw new IllegalArgumentException("INVALID_TARGET_UUID"); }
    }

    private static BlockPos position(JsonObject object) {
        if (object.has("position") && object.get("position").isJsonObject()) {
            return position(object.getAsJsonObject("position"));
        }
        if (object.has("target") && object.get("target").isJsonObject()) {
            return position(object.getAsJsonObject("target"));
        }
        return new BlockPos(
                (int) Math.floor(number(object, "x")),
                (int) Math.floor(number(object, "y")),
                (int) Math.floor(number(object, "z"))
        );
    }

    private static BlockPos position(JsonObject object, String key) {
        if (!object.has(key) || !object.get(key).isJsonObject()) {
            throw new IllegalArgumentException("MISSING_" + key.toUpperCase(Locale.ROOT));
        }
        return position(object.getAsJsonObject(key));
    }

    private static Item item(String id) {
        ResourceLocation key = ResourceLocation.tryParse(id);
        if (key == null || !BuiltInRegistries.ITEM.containsKey(key)) {
            throw new IllegalArgumentException("UNKNOWN_ITEM");
        }
        return BuiltInRegistries.ITEM.get(key);
    }

    private static Item optionalItem(String id) {
        return id == null || id.isBlank() ? null : item(id);
    }

    private static Direction direction(String value) {
        try { return Direction.valueOf(value.toUpperCase(Locale.ROOT)); }
        catch (Exception ignored) { return Direction.UP; }
    }

    private static EquipmentSlot slot(String value) {
        try { return EquipmentSlot.valueOf(value.toUpperCase(Locale.ROOT)); }
        catch (Exception ignored) { return EquipmentSlot.MAINHAND; }
    }

    private static net.minecraft.world.InteractionHand hand(String value) {
        return "OFF_HAND".equalsIgnoreCase(value)
                ? net.minecraft.world.InteractionHand.OFF_HAND
                : net.minecraft.world.InteractionHand.MAIN_HAND;
    }
}
