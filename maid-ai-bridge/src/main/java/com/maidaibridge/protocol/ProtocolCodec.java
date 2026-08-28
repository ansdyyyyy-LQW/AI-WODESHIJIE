package com.maidaibridge.protocol;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.time.Instant;
import java.util.UUID;

public final class ProtocolCodec {
    public static final int VERSION = 1;
    private static final Gson GSON = new GsonBuilder().disableHtmlEscaping().create();

    public static JsonObject parse(String text) {
        JsonElement element = JsonParser.parseString(text);
        if (!element.isJsonObject()) throw new IllegalArgumentException("protocol envelope must be an object");
        JsonObject object = element.getAsJsonObject();
        int version = object.has("protocol_version") ? object.get("protocol_version").getAsInt() : 0;
        if (version != VERSION) throw new IllegalArgumentException("unsupported protocol version " + version);
        return object;
    }

    public static JsonObject envelope(String type, JsonObject payload, String sessionId, UUID maidUuid, long gameTick) {
        JsonObject object = new JsonObject();
        object.addProperty("protocol_version", VERSION);
        object.addProperty("type", type);
        object.addProperty("session_id", sessionId == null ? "" : sessionId);
        object.addProperty("message_id", UUID.randomUUID().toString());
        if (maidUuid != null) object.addProperty("maid_uuid", maidUuid.toString());
        object.addProperty("game_tick", gameTick);
        object.addProperty("timestamp_ms", Instant.now().toEpochMilli());
        object.add("payload", payload == null ? new JsonObject() : payload);
        return object;
    }

    public static String encode(JsonObject envelope) { return GSON.toJson(envelope); }
    public static JsonObject payload(JsonObject envelope) {
        return envelope.has("payload") && envelope.get("payload").isJsonObject()
                ? envelope.getAsJsonObject("payload") : new JsonObject();
    }
    public static String string(JsonObject object, String key, String fallback) {
        try { return object.has(key) && !object.get(key).isJsonNull() ? object.get(key).getAsString() : fallback; }
        catch (RuntimeException ignored) { return fallback; }
    }
    public static int integer(JsonObject object, String key, int fallback) {
        try { return object.has(key) ? object.get(key).getAsInt() : fallback; }
        catch (RuntimeException ignored) { return fallback; }
    }
    public static double number(JsonObject object, String key, double fallback) {
        try { return object.has(key) ? object.get(key).getAsDouble() : fallback; }
        catch (RuntimeException ignored) { return fallback; }
    }

    private ProtocolCodec() {}
}
