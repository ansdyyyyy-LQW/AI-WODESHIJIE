package com.maidaibridge.action.impl;
import com.maidaibridge.action.AbstractMaidAction;import com.maidaibridge.action.ActionContext;
public final class StopAction extends AbstractMaidAction {public StopAction(String req){super(req,"stop",20);}@Override protected void onTick(ActionContext ctx){ctx.motion().forceStop(ctx.maid());succeed("STOPPED");}}
