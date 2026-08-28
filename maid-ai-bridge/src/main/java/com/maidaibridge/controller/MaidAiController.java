package com.maidaibridge.controller;

import com.github.tartaricacid.touhoulittlemaid.api.task.IMaidTask;
import com.github.tartaricacid.touhoulittlemaid.entity.passive.EntityMaid;
import com.github.tartaricacid.touhoulittlemaid.entity.task.TaskManager;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.maidaibridge.BridgeConfig;
import com.maidaibridge.MaidAiBridgeMod;
import com.maidaibridge.action.ActionEngine;
import com.maidaibridge.action.CancelReason;
import com.maidaibridge.action.MaidAction;
import com.maidaibridge.discover.MaidDiscoveryService;
import com.maidaibridge.inventory.MaidInventoryService;
import com.maidaibridge.observe.MaidObservationService;
import com.maidaibridge.persist.MaidAiSavedData;
import com.maidaibridge.protocol.ProtocolCodec;
import com.maidaibridge.reflex.ReflexEngine;
import com.maidaibridge.task.MaidAutonomousTask;
import com.maidaibridge.transport.MaidWsClient;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.damagesource.DamageSource;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.LivingEntity;
import net.minecraftforge.fml.ModList;

import java.util.Optional;
import java.util.UUID;

public final class MaidAiController {
    private static final String OLD_TASK_TAG = "MaidAI.OldTask";
    private static final String OLD_HOME_TAG = "MaidAI.OldHome";

    private final MotionArbiter motion = new MotionArbiter();
    private final MaidInventoryService inventory = new MaidInventoryService();
    private final MaidDiscoveryService discovery = new MaidDiscoveryService();
    private final MaidObservationService observations = new MaidObservationService(inventory);
    private final ReflexEngine reflex = new ReflexEngine();
    private final String sessionId = UUID.randomUUID().toString();

    private MinecraftServer server;
    private MaidWsClient ws;
    private ActionEngine actions;
    private MaidBridgeEventPublisher events;
    private UUID boundMaid;
    private boolean autonomyEnabled;
    private boolean previouslyConnected;
    private long tick;
    private long lastProtocolPing;
    private MaidAiSavedData.PendingAction interruptedAction;

    public void start(MinecraftServer server) {
        this.server = server;
        this.tick = server.getTickCount();
        MaidAiSavedData saved = MaidAiSavedData.get(server);
        this.boundMaid = saved.boundMaid().orElse(null);
        this.autonomyEnabled = saved.autonomyEnabled();
        this.interruptedAction = saved.pendingAction().orElse(null);
        saved.clearPendingAction();
        this.ws = new MaidWsClient(BridgeConfig.WEBSOCKET_URL.get());
        this.events = new MaidBridgeEventPublisher(sessionId, this::send, this::connected);
        this.actions = new ActionEngine(
                motion,
                inventory,
                observations,
                this::send,
                events,
                this::checkpointAction,
                this::clearActionCheckpoint
        );
        MaidAiBridgeMod.LOGGER.info(
                "Maid AI controller started; persisted maid={}, autonomy={}",
                boundMaid, autonomyEnabled
        );
    }

    public void stop() {
        EntityMaid maid = resolve().orElse(null);
        if (maid != null && actions != null) {
            actions.cancel(maid, (ServerLevel) maid.level(), tick, CancelReason.SERVER_STOPPING);
            reflex.stop(maid, motion, actions);
        }
        if (ws != null) ws.close();
        server = null;
    }

    public void tick() {
        if (server == null || ws == null || actions == null) return;
        tick = server.getTickCount();
        ws.tick(this::handle);
        boolean connected = ws.isConnected();
        if (ws.consumeJustConnected()) {
            sendHello();
            sendInterruptedActionIfNeeded();
            events.flush();
        }
        if (previouslyConnected && !connected) safeIdle("AGENT_DISCONNECTED");
        previouslyConnected = connected;

        Optional<EntityMaid> maidOptional = resolve();
        if (maidOptional.isPresent()) {
            EntityMaid maid = maidOptional.get();
            ServerLevel level = (ServerLevel) maid.level();
            if (autonomyEnabled) ensureAutonomousTask(maid, level);
            if (connected && autonomyEnabled) {
                String previousReflex = reflex.state();
                reflex.tick(maid, level, tick, actions, motion);
                if (!reflex.state().equals(previousReflex) && !"NONE".equals(reflex.state())) {
                    JsonObject reflexData = new JsonObject();
                    reflexData.addProperty("reflex", reflex.state());
                    events.emit(reflex.state().contains("RETREAT") ? "RETREAT" : "REFLEX_TRIGGERED", "WARN", maid, level, tick, reflexData);
                }
                actions.tick(maid, level, tick);
                int period = Math.max(1, 20 / BridgeConfig.SNAPSHOT_HZ.get());
                if (tick % period == 0) {
                    JsonObject snapshot = observations.snapshot(maid, level, actions);
                    send("STATE_SNAPSHOT", snapshot);
                    events.observeSnapshot(maid, level, tick, snapshot);
                }
            } else {
                motion.forceStop(maid);
            }
        }
        if (connected && tick - lastProtocolPing >= 100) {
            JsonObject payload = new JsonObject();
            payload.addProperty("tick", tick);
            send("PING", payload);
            lastProtocolPing = tick;
        }
    }

    private void handle(JsonObject envelope) {
        String type = ProtocolCodec.string(envelope, "type", "");
        JsonObject payload = ProtocolCodec.payload(envelope);
        switch (type) {
            case "PING" -> {
                JsonObject pong = new JsonObject();
                pong.addProperty("echo", ProtocolCodec.string(envelope, "message_id", ""));
                send("PONG", pong);
            }
            case "PONG" -> { }
            case "STATE_RESYNC" -> resolve().ifPresent(maid -> sendSnapshot(maid, (ServerLevel) maid.level()));
            case "DISCOVER_MAIDS" -> {
                JsonObject output = new JsonObject();
                output.addProperty("request_id", ProtocolCodec.string(
                        payload, "request_id", ProtocolCodec.string(envelope, "message_id", "")
                ));
                output.add("maids", discovery.discover(server, ProtocolCodec.string(payload, "owner_uuid", "")));
                send("MAID_LIST", output);
            }
            case "LIST_PLAYERS" -> {
                JsonObject output = new JsonObject();
                output.addProperty("request_id", ProtocolCodec.string(
                        payload, "request_id", ProtocolCodec.string(envelope, "message_id", "")
                ));
                output.add("players", discovery.players(server));
                send("PLAYER_LIST", output);
            }
            case "BIND_MAID" -> {
                String requestId = ProtocolCodec.string(payload, "request_id", "");
                try {
                    UUID id = UUID.fromString(ProtocolCodec.string(payload, "maid_uuid", ""));
                    String owner = ProtocolCodec.string(payload, "owner_uuid", "");
                    bind(id, owner);
                    controlResult(requestId, true, "BOUND", "女仆已绑定");
                } catch (Exception exception) {
                    controlResult(requestId, false, "MAID_NOT_FOUND", exception.getMessage());
                }
            }
            case "UNBIND_MAID" -> {
                String requestId = ProtocolCodec.string(payload, "request_id", "");
                unbind();
                controlResult(requestId, true, "UNBOUND", "女仆已解绑");
            }
            case "SAFE_IDLE" -> safeIdle(ProtocolCodec.string(payload, "reason", "agent requested safe idle"));
            case "ACTION_REQUEST" -> {
                Optional<EntityMaid> maid = resolve();
                if (maid.isEmpty() || !autonomyEnabled) {
                    JsonObject result = new JsonObject();
                    String requestId = ProtocolCodec.string(payload, "request_id", "");
                    result.addProperty("request_id", requestId);
                    result.addProperty("action_id", UUID.randomUUID().toString());
                    result.addProperty("status", "FAILED");
                    result.addProperty("code", maid.isEmpty() ? "MAID_NOT_BOUND" : "AUTONOMY_DISABLED");
                    result.add("data", new JsonObject());
                    result.add("world_delta", new JsonObject());
                    send("ACTION_RESULT", result);
                } else {
                    actions.submit(payload, maid.get(), (ServerLevel) maid.get().level(), tick);
                }
            }
            default -> MaidAiBridgeMod.LOGGER.debug("Ignoring unsupported Agent message type {}", type);
        }
    }

    public void bind(UUID id, String requiredOwnerUuid) {
        EntityMaid maid = discovery.find(server, id)
                .orElseThrow(() -> new IllegalArgumentException("未找到已加载且存活的女仆"));
        if (requiredOwnerUuid == null || requiredOwnerUuid.isBlank()) {
            throw new IllegalArgumentException("外部绑定必须提供当前在线主人");
        }
        UUID owner = maid.getOwnerUUID();
        if (owner == null || !owner.toString().equalsIgnoreCase(requiredOwnerUuid)) {
            throw new IllegalArgumentException("女仆不属于已选择玩家");
        }
        if (boundMaid != null && !boundMaid.equals(id)) unbind();
        IMaidTask current = maid.getTask();
        if (current != null && !current.getUid().equals(MaidAutonomousTask.UID)) {
            maid.getPersistentData().putString(OLD_TASK_TAG, current.getUid().toString());
        }
        maid.getPersistentData().putBoolean(OLD_HOME_TAG, maid.isHomeModeEnable());
        boundMaid = id;
        autonomyEnabled = true;
        MaidAiSavedData data = MaidAiSavedData.get(server);
        data.setBoundMaid(id);
        data.setAutonomyEnabled(true);
        ensureAutonomousTask(maid, (ServerLevel) maid.level());
        sendSnapshot(maid, (ServerLevel) maid.level());
    }

    public void unbind() {
        Optional<EntityMaid> maidOptional = resolve();
        if (maidOptional.isPresent()) {
            EntityMaid maid = maidOptional.get();
            ServerLevel level = (ServerLevel) maid.level();
            actions.cancel(maid, level, tick, CancelReason.USER);
            reflex.stop(maid, motion, actions);
            String old = maid.getPersistentData().getString(OLD_TASK_TAG);
            ResourceLocation uid = ResourceLocation.tryParse(old);
            IMaidTask task = uid == null
                    ? TaskManager.getIdleTask()
                    : TaskManager.findTask(uid).orElse(TaskManager.getIdleTask());
            maid.setTask(task);
            maid.setHomeModeEnable(maid.getPersistentData().getBoolean(OLD_HOME_TAG));
            maid.refreshBrain(level);
            maid.getPersistentData().remove(OLD_TASK_TAG);
            maid.getPersistentData().remove(OLD_HOME_TAG);
        }
        boundMaid = null;
        autonomyEnabled = false;
        MaidAiSavedData data = MaidAiSavedData.get(server);
        data.clearBoundMaid();
        data.setAutonomyEnabled(false);
        data.clearPendingAction();
    }

    private void ensureAutonomousTask(EntityMaid maid, ServerLevel level) {
        IMaidTask task = TaskManager.findTask(MaidAutonomousTask.UID).orElse(null);
        if (task != null && (maid.getTask() == null || !maid.getTask().getUid().equals(MaidAutonomousTask.UID))) {
            maid.setOrderedToSit(false);
            maid.setHomeModeEnable(false);
            maid.setTask(task);
            maid.refreshBrain(level);
        }
    }

    public Optional<EntityMaid> resolve() {
        return server == null || boundMaid == null
                ? Optional.empty() : discovery.find(server, boundMaid);
    }

    public JsonArray discover() {
        return server == null ? new JsonArray() : discovery.discover(server, "");
    }

    public UUID boundMaid() { return boundMaid; }
    public boolean connected() { return ws != null && ws.isConnected(); }
    public boolean isBound(Entity entity) {
        return entity instanceof EntityMaid maid && boundMaid != null && maid.getUUID().equals(boundMaid);
    }

    public void safeIdle(String reason) {
        resolve().ifPresent(maid -> {
            ServerLevel level = (ServerLevel) maid.level();
            actions.cancel(maid, level, tick, CancelReason.SAFE_IDLE);
            reflex.stop(maid, motion, actions);
            motion.forceStop(maid);
            JsonObject data = new JsonObject();
            data.addProperty("reason", reason);
            events.emit("SAFE_IDLE_ENTERED", "WARN", maid, level, tick, data);
        });
        MaidAiBridgeMod.LOGGER.info("Bridge SAFE_IDLE: {}", reason);
    }

    public void reconnect() {
        if (server == null) return;
        if (ws != null) ws.close();
        ws = new MaidWsClient(BridgeConfig.WEBSOCKET_URL.get());
        previouslyConnected = false;
    }

    public void onLivingHurt(LivingEntity victim, DamageSource source, float amount) {
        runOnServer(() -> {
            if (!isBound(victim) || !(victim.level() instanceof ServerLevel level)) return;
            EntityMaid maid = (EntityMaid) victim;
            JsonObject data = new JsonObject();
            data.addProperty("amount", amount);
            data.addProperty("source", source.getMsgId());
            Entity attacker = source.getEntity();
            if (attacker != null) {
                data.addProperty("attacker_uuid", attacker.getUUID().toString());
                data.addProperty("attacker_type", BuiltInEntityId.of(attacker));
                maid.getPersistentData().putUUID("MaidAI.RecentAttacker", attacker.getUUID());
                maid.getPersistentData().putLong("MaidAI.RecentAttackerTick", tick);
            }
            events.emit("DAMAGE_TAKEN", amount >= 6 ? "WARN" : "INFO", maid, level, tick, data);
        });
    }

    public void onLivingDeath(LivingEntity victim, DamageSource source) {
        runOnServer(() -> {
            if (isBound(victim) && victim.level() instanceof ServerLevel level) {
                EntityMaid maid = (EntityMaid) victim;
                JsonObject data = new JsonObject();
                data.addProperty("source", source.getMsgId());
                events.emit("MAID_DEATH", "CRITICAL", maid, level, tick, data);
            }
            Entity killer = source.getEntity();
            if (killer != null && isBound(killer) && killer.level() instanceof ServerLevel level) {
                EntityMaid maid = (EntityMaid) killer;
                JsonObject data = new JsonObject();
                data.addProperty("victim_uuid", victim.getUUID().toString());
                data.addProperty("victim_type", BuiltInEntityId.of(victim));
                events.emit("ENTITY_KILLED", "INFO", maid, level, tick, data);
            }
        });
    }

    public void onWorldSave(ServerLevel level) {
        runOnServer(() -> resolve().ifPresent(maid -> {
            if (maid.level() != level) return;
            events.emit("WORLD_SAVED", "INFO", maid, level, tick, new JsonObject());
        }));
    }

    private void sendHello() {
        JsonObject payload = new JsonObject();
        payload.addProperty("bridge_version", MaidAiBridgeMod.VERSION);
        payload.addProperty("minecraft", "1.20.1");
        payload.addProperty("forge", modVersion("forge"));
        payload.addProperty("tlm", modVersion("touhou_little_maid"));
        payload.addProperty("protocol_version", ProtocolCodec.VERSION);
        JsonArray capabilities = new JsonArray();
        for (String value : new String[]{
                "observe.status", "observe.visible_blocks", "observe.place_position", "event.gameplay",
                "observe.entity", "observe.nearby_entities", "observe.block", "observe.local_space",
                "action.move_to", "action.break_block", "action.place_block",
                "action.face", "action.relative_move", "action.distance_control", "action.body_control",
                "action.entity_interaction", "action.hand_use", "action.block_interaction",
                "action.inventory", "action.container", "action.wait", "action.wait_until",
                "action.craft", "action.smelt", "action.combat", "action.region",
                "action.build_chunk", "maid.discovery", "player.discovery", "maid.binding", "request.replay"
        }) capabilities.add(value);
        payload.add("capabilities", capabilities);
        send("HELLO", payload);
    }

    private static String modVersion(String modId) {
        return ModList.get().getModContainerById(modId)
                .map(container -> container.getModInfo().getVersion().toString())
                .orElse("unknown");
    }

    private void sendSnapshot(EntityMaid maid, ServerLevel level) {
        JsonObject snapshot = observations.snapshot(maid, level, actions);
        send("STATE_SNAPSHOT", snapshot);
        events.observeSnapshot(maid, level, tick, snapshot);
    }

    private void controlResult(String requestId, boolean ok, String code, String message) {
        JsonObject payload = new JsonObject();
        payload.addProperty("request_id", requestId);
        payload.addProperty("ok", ok);
        payload.addProperty("code", code);
        payload.addProperty("message", message == null ? "" : message);
        send("CONTROL_RESULT", payload);
    }

    private void send(String type, JsonObject payload) {
        if (ws == null) return;
        ws.send(ProtocolCodec.envelope(type, payload, sessionId, boundMaid, tick));
    }

    private void checkpointAction(MaidAction action) {
        if (server == null) return;
        MaidAiSavedData.get(server).setPendingAction(action.requestId(), action.id(), action.type());
    }

    private void clearActionCheckpoint() {
        if (server != null) MaidAiSavedData.get(server).clearPendingAction();
    }

    private void sendInterruptedActionIfNeeded() {
        if (interruptedAction == null) return;
        JsonObject result = new JsonObject();
        result.addProperty("request_id", interruptedAction.requestId());
        result.addProperty("action_id", interruptedAction.actionId());
        result.addProperty("status", "CANCELLED");
        result.addProperty("code", "INTERRUPTED_BY_RESTART");
        JsonObject data = new JsonObject();
        data.addProperty("action", interruptedAction.actionType());
        data.addProperty("message", "服务器重启中断了未完成动作；Runtime 必须重新验证后再计划。");
        result.add("data", data);
        result.add("world_delta", new JsonObject());
        send("ACTION_RESULT", result);
        interruptedAction = null;
    }

    private void runOnServer(Runnable runnable) {
        MinecraftServer current = server;
        if (current == null) return;
        if (current.isSameThread()) runnable.run();
        else current.execute(runnable);
    }

    private static final class BuiltInEntityId {
        static String of(Entity entity) {
            return net.minecraft.core.registries.BuiltInRegistries.ENTITY_TYPE
                    .getKey(entity.getType()).toString();
        }
    }
}
