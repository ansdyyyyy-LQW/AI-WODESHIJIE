package com.maidaibridge;

import net.minecraftforge.common.ForgeConfigSpec;

public final class BridgeConfig {
    public static final ForgeConfigSpec SPEC;
    public static final ForgeConfigSpec.ConfigValue<String> WEBSOCKET_URL;
    public static final ForgeConfigSpec.IntValue SNAPSHOT_HZ;
    public static final ForgeConfigSpec.IntValue ENTITY_OBSERVE_RADIUS;
    public static final ForgeConfigSpec.IntValue VISIBLE_BLOCK_RADIUS;
    public static final ForgeConfigSpec.BooleanValue STRICT_SURVIVAL;
    public static final ForgeConfigSpec.BooleanValue ALLOW_HIDDEN_BLOCK_SCAN;
    public static final ForgeConfigSpec.BooleanValue ALLOW_REMOTE_WORLD_EDIT;
    public static final ForgeConfigSpec.IntValue ACTION_TIMEOUT_TICKS;
    public static final ForgeConfigSpec.ConfigValue<String> EXTRA_HOSTILE_ENTITY_TYPES;

    static {
        ForgeConfigSpec.Builder builder = new ForgeConfigSpec.Builder();
        builder.push("connection");
        WEBSOCKET_URL = builder.comment("Local Agent Core WebSocket. Only localhost is permitted by default.")
                .define("websocket_url", "ws://127.0.0.1:8765");
        SNAPSHOT_HZ = builder.defineInRange("snapshot_hz", 2, 1, 10);
        builder.pop();
        builder.push("observation");
        ENTITY_OBSERVE_RADIUS = builder.defineInRange("entity_observe_radius", 32, 4, 96);
        VISIBLE_BLOCK_RADIUS = builder.defineInRange("visible_block_radius", 12, 2, 24);
        ALLOW_HIDDEN_BLOCK_SCAN = builder.define("allow_hidden_block_scan", false);
        EXTRA_HOSTILE_ENTITY_TYPES = builder.comment("Comma-separated entity IDs that should be treated as hostile.")
                .define("extra_hostile_entity_types", "");
        builder.pop();
        builder.push("capability_policy");
        STRICT_SURVIVAL = builder.define("strict_survival", true);
        ALLOW_REMOTE_WORLD_EDIT = builder.define("allow_remote_world_edit", false);
        ACTION_TIMEOUT_TICKS = builder.defineInRange("action_timeout_ticks", 1200, 20, 72000);
        builder.pop();
        SPEC = builder.build();
    }

    private BridgeConfig() {}
}
