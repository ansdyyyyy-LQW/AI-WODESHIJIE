package com.maidaibridge.action;

import com.google.gson.JsonObject;
import java.util.UUID;

public record ActionResult(UUID actionId, String requestId, ActionState state, String code, JsonObject data, JsonObject worldDelta) {
    public JsonObject toPayload() {
        JsonObject out=new JsonObject();out.addProperty("request_id",requestId);out.addProperty("action_id",actionId.toString());
        String status=switch(state){case SUCCESS->"SUCCESS";case CANCELLED->"CANCELLED";case PREEMPTED->"PREEMPTED";case TIMEOUT->"TIMEOUT";default->"FAILED";};
        out.addProperty("status",status);out.addProperty("code",code);out.add("data",data==null?new JsonObject():data);out.add("world_delta",worldDelta==null?new JsonObject():worldDelta);return out;
    }
}
