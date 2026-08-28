package com.maidaibridge.action.impl;
import com.maidaibridge.action.AbstractMaidAction;import com.maidaibridge.action.ActionContext;
public final class LookAtAction extends AbstractMaidAction {private final double x,y,z;public LookAtAction(String req,int timeout,double x,double y,double z){super(req,"look_at",timeout);this.x=x;this.y=y;this.z=z;}@Override protected void onTick(ActionContext ctx){ctx.maid().getLookControl().setLookAt(x,y,z,30,30);succeed("LOOKING");}}
