package com.maidaibridge.controller;
import java.util.UUID;
public record MotionLease(UUID actionId, MotionPriority priority, long acquiredTick) {}
