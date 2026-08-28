package com.maidaibridge.controller;

import com.github.tartaricacid.touhoulittlemaid.entity.passive.EntityMaid;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.server.level.ServerLevel;

import java.nio.charset.StandardCharsets;
import java.util.ArrayDeque;
import java.util.LinkedHashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.function.BiConsumer;
import java.util.function.BooleanSupplier;

/** Publishes bounded, deduplicated gameplay events to Agent Core. */
public final class MaidBridgeEventPublisher {
    private static final int MAX_PENDING = 512;
    private static final int MAX_DEDUPE = 2048;

    private final String sessionId;
    private final BiConsumer<String, JsonObject> sender;
    private final BooleanSupplier connected;
    private final ArrayDeque<JsonObject> pending = new ArrayDeque<>();
    private final LinkedHashMap<String, Long> recentKeys = new LinkedHashMap<>();
    private long sequence;
    private long lastDay = Long.MIN_VALUE;
    private String lastInventoryFingerprint = "";
    private int lastNearbyHostiles;
    private Set<String> lastNearbyHostileIds = Set.of();

    public MaidBridgeEventPublisher(
            String sessionId,
            BiConsumer<String, JsonObject> sender,
            BooleanSupplier connected
    ) {
        this.sessionId = sessionId;
        this.sender = sender;
        this.connected = connected;
    }

    public void emit(
            String eventType,
            String severity,
            EntityMaid maid,
            ServerLevel level,
            long gameTick,
            JsonObject data
    ) {
        String key = dedupeKey(eventType, maid, gameTick, data);
        Long previous = recentKeys.get(key);
        if (previous != null && gameTick - previous <= 2) {
            return;
        }
        recentKeys.put(key, gameTick);
        while (recentKeys.size() > MAX_DEDUPE) {
            String first = recentKeys.keySet().iterator().next();
            recentKeys.remove(first);
        }

        JsonObject payload = new JsonObject();
        String eventId = UUID.nameUUIDFromBytes(
                (sessionId + ":" + (++sequence) + ":" + eventType + ":" + gameTick)
                        .getBytes(StandardCharsets.UTF_8)
        ).toString();
        payload.addProperty("event_id", eventId);
        payload.addProperty("event_type", eventType);
        payload.addProperty("severity", severity);
        payload.addProperty("game_tick", gameTick);
        payload.addProperty("game_day", level.getDayTime() / 24000L);
        long timeOfDay = Math.floorMod(level.getDayTime(), 24000L);
        String period = timeOfDay < 13000L ? "DAY" : "NIGHT";
        payload.addProperty("time_of_day", timeOfDay);
        payload.addProperty("period", period);
        payload.addProperty("maid_uuid", maid.getUUID().toString());
        JsonObject position = new JsonObject();
        position.addProperty("x", maid.getX());
        position.addProperty("y", maid.getY());
        position.addProperty("z", maid.getZ());
        payload.add("position", position);
        JsonObject eventData = data == null ? new JsonObject() : data.deepCopy();
        eventData.addProperty("time_of_day", timeOfDay);
        eventData.addProperty("period", period);
        payload.add("data", eventData);
        deliver(payload);
    }

    public void observeSnapshot(EntityMaid maid, ServerLevel level, long gameTick, JsonObject snapshot) {
        long day = level.getDayTime() / 24000L;
        if (lastDay != day) {
            JsonObject data = new JsonObject();
            data.addProperty("previous_day", lastDay == Long.MIN_VALUE ? -1 : lastDay);
            data.addProperty("day", day);
            emit("GAME_DAY_CHANGED", "INFO", maid, level, gameTick, data);
            lastDay = day;
        }

        JsonArray inventory = snapshot.has("inventory") && snapshot.get("inventory").isJsonArray()
                ? snapshot.getAsJsonArray("inventory") : new JsonArray();
        String fingerprint = inventory.toString();
        if (!lastInventoryFingerprint.isEmpty() && !lastInventoryFingerprint.equals(fingerprint)) {
            JsonObject data = new JsonObject();
            data.add("inventory", inventory.deepCopy());
            emit("INVENTORY_CHANGED", "INFO", maid, level, gameTick, data);
        }
        lastInventoryFingerprint = fingerprint;

        int hostiles = 0;
        Set<String> hostileIds = new HashSet<>();
        JsonArray hostileRows = new JsonArray();
        if (snapshot.has("nearby_entities") && snapshot.get("nearby_entities").isJsonArray()) {
            for (var element : snapshot.getAsJsonArray("nearby_entities")) {
                if (!element.isJsonObject()) continue;
                JsonObject row = element.getAsJsonObject();
                String category = row.has("category") ? row.get("category").getAsString() : "";
                double distance = row.has("distance") ? row.get("distance").getAsDouble() : Double.MAX_VALUE;
                if (("HOSTILE".equals(category) || "MONSTER".equals(category) || "ENEMY".equals(category)) && distance <= 24.0) {
                    hostiles++;
                    String id = row.has("uuid") ? row.get("uuid").getAsString() : "";
                    if (!id.isBlank()) hostileIds.add(id);
                    hostileRows.add(row.deepCopy());
                }
            }
        }
        if (hostiles > 0 && (lastNearbyHostiles == 0 || hostiles >= lastNearbyHostiles + 3)) {
            JsonObject data = new JsonObject();
            data.addProperty("count", hostiles);
            data.add("entities", hostileRows);
            JsonObject firstNew = null;
            for (var element : hostileRows) {
                JsonObject row = element.getAsJsonObject();
                String id = row.has("uuid") ? row.get("uuid").getAsString() : "";
                if (!lastNearbyHostileIds.contains(id)) { firstNew = row; break; }
            }
            if (firstNew != null) {
                if (firstNew.has("uuid")) data.addProperty("entity_uuid", firstNew.get("uuid").getAsString());
                if (firstNew.has("type")) data.addProperty("entity_type", firstNew.get("type").getAsString());
                if (firstNew.has("relative")) data.add("relative", firstNew.get("relative").deepCopy());
            }
            emit(hostiles >= 5 ? "HOSTILE_WAVE_DETECTED" : "HOSTILE_CONTACT", hostiles >= 5 ? "WARN" : "INFO", maid, level, gameTick, data);
        }
        lastNearbyHostiles = hostiles;
        lastNearbyHostileIds = Set.copyOf(hostileIds);
    }

    public void actionStarted(EntityMaid maid, ServerLevel level, long gameTick, String requestId, String actionId, String action) {
        JsonObject data = new JsonObject();
        data.addProperty("request_id", requestId);
        data.addProperty("action_id", actionId);
        data.addProperty("action", action);
        emit("ACTION_STARTED", "INFO", maid, level, gameTick, data);
    }

    public void actionFinished(EntityMaid maid, ServerLevel level, long gameTick, JsonObject result) {
        JsonObject data = result.deepCopy();
        String status = result.has("status") ? result.get("status").getAsString() : "FAILED";
        String code = result.has("code") ? result.get("code").getAsString() : "UNKNOWN";
        String event = "SUCCESS".equals(status) ? "ACTION_SUCCEEDED"
                : ("STUCK".equals(code) ? "ACTION_STUCK" : "ACTION_FAILED");
        emit(event, "SUCCESS".equals(status) ? "INFO" : "WARN", maid, level, gameTick, data);
        if ("SUCCESS".equals(status) && "BROKEN".equals(code)
                && result.has("data") && result.get("data").isJsonObject()) {
            JsonObject actionData = result.getAsJsonObject("data");
            if (actionData.has("block_id") && actionData.has("position")) {
                JsonObject broken = new JsonObject();
                broken.addProperty("request_id", result.has("request_id") ? result.get("request_id").getAsString() : "");
                broken.addProperty("block_id", actionData.get("block_id").getAsString());
                JsonObject position = actionData.getAsJsonObject("position");
                broken.addProperty("x", position.get("x").getAsInt());
                broken.addProperty("y", position.get("y").getAsInt());
                broken.addProperty("z", position.get("z").getAsInt());
                broken.addProperty("dimension", level.dimension().location().toString());
                emit("BLOCK_BROKEN", "INFO", maid, level, gameTick, broken);
            }
        }
    }

    public void flush() {
        if (!connected.getAsBoolean()) return;
        while (!pending.isEmpty()) sender.accept("EVENT", pending.removeFirst());
    }

    private void deliver(JsonObject payload) {
        if (connected.getAsBoolean()) {
            sender.accept("EVENT", payload);
            return;
        }
        if (pending.size() >= MAX_PENDING) pending.removeFirst();
        pending.addLast(payload);
    }

    private static String dedupeKey(String type, EntityMaid maid, long tick, JsonObject data) {
        // Two identical hook calls in the same server-tick collapse; events at different
        // ticks remain separate and preserve combat metrics.
        return type + "|" + maid.getUUID() + "|" + tick + "|" + (data == null ? "" : data.toString());
    }
}
