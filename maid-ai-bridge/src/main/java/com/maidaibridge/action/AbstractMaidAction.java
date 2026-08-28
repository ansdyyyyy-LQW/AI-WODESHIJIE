package com.maidaibridge.action;

import com.google.gson.JsonObject;
import java.util.UUID;

public abstract class AbstractMaidAction implements MaidAction {
    protected final UUID id=UUID.randomUUID();protected final String requestId;protected final String type;protected final int timeoutTicks;
    protected ActionState state=ActionState.NEW;protected long startTick;protected String code="PENDING";protected JsonObject data=new JsonObject();protected JsonObject worldDelta=new JsonObject();
    protected AbstractMaidAction(String requestId,String type,int timeoutTicks){this.requestId=requestId;this.type=type;this.timeoutTicks=Math.max(1,timeoutTicks);}
    @Override public UUID id(){return id;}@Override public String requestId(){return requestId;}@Override public String type(){return type;}@Override public ActionState state(){return state;}
    @Override public final void start(ActionContext ctx){if(state!=ActionState.NEW)return;state=ActionState.RUNNING;startTick=ctx.gameTick();onStart(ctx);}
    @Override public final void tick(ActionContext ctx){if(state==ActionState.NEW)start(ctx);if(state!=ActionState.RUNNING)return;if(ctx.gameTick()-startTick>timeoutTicks){state=ActionState.TIMEOUT;code=timeoutCode();onStop(ctx);return;}onTick(ctx);}
    protected void onStart(ActionContext ctx){} protected abstract void onTick(ActionContext ctx);protected void onStop(ActionContext ctx){}
    protected String timeoutCode(){return "TIMEOUT";}
    protected final void succeed(String value){state=ActionState.SUCCESS;code=value;}protected final void fail(String value){state=ActionState.FAILED;code=value;}
    @Override public void cancel(ActionContext ctx,CancelReason reason){if(isTerminal())return;state=reason==CancelReason.PREEMPTED_BY_REFLEX?ActionState.PREEMPTED:ActionState.CANCELLED;code=reason.name();onStop(ctx);}
    @Override public boolean isTerminal(){return state!=ActionState.NEW&&state!=ActionState.RUNNING;}
    @Override public ActionResult result(){return new ActionResult(id,requestId,state,code,data,worldDelta);}
    @Override public JsonObject summary(){JsonObject out=new JsonObject();out.addProperty("action_id",id.toString());out.addProperty("action",type);out.addProperty("state",state.name());out.addProperty("code",code);return out;}
}
