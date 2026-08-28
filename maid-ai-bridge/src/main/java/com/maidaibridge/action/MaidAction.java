package com.maidaibridge.action;

import com.google.gson.JsonObject;
import java.util.UUID;

public interface MaidAction {
    UUID id(); String requestId(); String type(); ActionState state();
    void start(ActionContext context); void tick(ActionContext context); void cancel(ActionContext context, CancelReason reason);
    boolean isTerminal(); ActionResult result(); JsonObject summary();
}
