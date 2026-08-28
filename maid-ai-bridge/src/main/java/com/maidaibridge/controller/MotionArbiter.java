package com.maidaibridge.controller;

import com.github.tartaricacid.touhoulittlemaid.entity.passive.EntityMaid;
import net.minecraft.world.entity.Entity;

import java.util.Optional;
import java.util.UUID;
import java.util.function.Consumer;

/** The only production owner allowed to write EntityMaid navigation. */
public final class MotionArbiter {
    private MotionLease current;
    private Consumer<MotionLease> preemptionListener = ignored -> {};

    public void setPreemptionListener(Consumer<MotionLease> listener) {
        this.preemptionListener = listener == null ? ignored -> {} : listener;
    }

    public Optional<MotionLease> acquire(UUID actionId, MotionPriority priority, long gameTick) {
        MotionLease preempted = null;
        MotionLease acquired;
        synchronized (this) {
            if (current == null || current.actionId().equals(actionId)) {
                current = new MotionLease(actionId, priority, gameTick);
                acquired = current;
            } else if (priority.value > current.priority().value) {
                preempted = current;
                current = new MotionLease(actionId, priority, gameTick);
                acquired = current;
            } else {
                return Optional.empty();
            }
        }
        if (preempted != null) preemptionListener.accept(preempted);
        return Optional.of(acquired);
    }

    public synchronized boolean owns(UUID actionId) {
        return current != null && current.actionId().equals(actionId);
    }

    public synchronized Optional<MotionLease> current() { return Optional.ofNullable(current); }

    public synchronized boolean moveTo(UUID actionId, EntityMaid maid, double x, double y, double z, double speed) {
        if (!owns(actionId)) return false;
        return maid.getNavigation().moveTo(x, y, z, speed);
    }

    public synchronized boolean moveTo(UUID actionId, EntityMaid maid, Entity target, double speed) {
        if (!owns(actionId)) return false;
        return maid.getNavigation().moveTo(target, speed);
    }

    /** Stop navigation but keep the lease, used by hold-position. */
    public synchronized void halt(UUID actionId, EntityMaid maid) {
        if (owns(actionId)) maid.getNavigation().stop();
    }

    public synchronized void stop(UUID actionId, EntityMaid maid) {
        if (!owns(actionId)) return;
        maid.getNavigation().stop();
        current = null;
    }

    public synchronized void release(UUID actionId, EntityMaid maid) {
        if (owns(actionId)) {
            maid.getNavigation().stop();
            current = null;
        }
    }

    public synchronized void forceStop(EntityMaid maid) {
        maid.getNavigation().stop();
        current = null;
    }
}
