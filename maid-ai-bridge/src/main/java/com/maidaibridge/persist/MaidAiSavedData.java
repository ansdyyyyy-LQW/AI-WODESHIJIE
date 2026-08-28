package com.maidaibridge.persist;

import net.minecraft.nbt.CompoundTag;
import net.minecraft.server.MinecraftServer;
import net.minecraft.world.level.saveddata.SavedData;

import java.util.Optional;
import java.util.UUID;

public final class MaidAiSavedData extends SavedData {
    public record PendingAction(String requestId, String actionId, String actionType) {}

    private UUID boundMaid;
    private boolean autonomyEnabled;
    private String pendingRequestId = "";
    private String pendingActionId = "";
    private String pendingActionType = "";

    public static MaidAiSavedData get(MinecraftServer server) {
        return server.overworld().getDataStorage().computeIfAbsent(
                MaidAiSavedData::load, MaidAiSavedData::new, "maid_ai_bridge"
        );
    }

    public static MaidAiSavedData load(CompoundTag tag) {
        MaidAiSavedData data = new MaidAiSavedData();
        if (tag.hasUUID("BoundMaid")) data.boundMaid = tag.getUUID("BoundMaid");
        data.autonomyEnabled = tag.getBoolean("AutonomyEnabled");
        data.pendingRequestId = tag.getString("PendingRequestId");
        data.pendingActionId = tag.getString("PendingActionId");
        data.pendingActionType = tag.getString("PendingActionType");
        return data;
    }

    @Override
    public CompoundTag save(CompoundTag tag) {
        if (boundMaid != null) tag.putUUID("BoundMaid", boundMaid);
        tag.putBoolean("AutonomyEnabled", autonomyEnabled);
        tag.putString("PendingRequestId", pendingRequestId);
        tag.putString("PendingActionId", pendingActionId);
        tag.putString("PendingActionType", pendingActionType);
        return tag;
    }

    public Optional<UUID> boundMaid() { return Optional.ofNullable(boundMaid); }
    public void setBoundMaid(UUID value) { boundMaid = value; setDirty(); }
    public void clearBoundMaid() { boundMaid = null; setDirty(); }
    public boolean autonomyEnabled() { return autonomyEnabled; }
    public void setAutonomyEnabled(boolean value) { autonomyEnabled = value; setDirty(); }

    public void setPendingAction(String requestId, UUID actionId, String actionType) {
        pendingRequestId = requestId == null ? "" : requestId;
        pendingActionId = actionId == null ? "" : actionId.toString();
        pendingActionType = actionType == null ? "" : actionType;
        setDirty();
    }

    public Optional<PendingAction> pendingAction() {
        if (pendingRequestId.isBlank() || pendingActionId.isBlank()) return Optional.empty();
        return Optional.of(new PendingAction(pendingRequestId, pendingActionId, pendingActionType));
    }

    public void clearPendingAction() {
        pendingRequestId = "";
        pendingActionId = "";
        pendingActionType = "";
        setDirty();
    }
}
