package com.maidaibridge.action.impl;

import com.google.gson.JsonObject;
import com.maidaibridge.action.AbstractMaidAction;
import com.maidaibridge.action.ActionContext;

public final class ImmediateAction extends AbstractMaidAction {
    private final String finalCode;private final JsonObject resultData;private final boolean ok;
    public ImmediateAction(String requestId,String type,int timeout,String code,JsonObject data,boolean ok){super(requestId,type,timeout);this.finalCode=code;this.resultData=data;this.ok=ok;}
    @Override protected void onTick(ActionContext ctx){this.data=resultData;if(ok)succeed(finalCode);else fail(finalCode);}
}
