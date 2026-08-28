package com.maidaibridge.controller;
public enum MotionPriority {
    IDLE(10), TASK(50), COMBAT(80), EMERGENCY_REFLEX(100);
    public final int value;
    MotionPriority(int value) { this.value = value; }
}
