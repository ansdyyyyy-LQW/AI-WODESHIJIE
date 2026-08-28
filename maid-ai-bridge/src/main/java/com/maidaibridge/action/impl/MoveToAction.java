package com.maidaibridge.action.impl;

import com.maidaibridge.action.AbstractMaidAction;
import com.maidaibridge.action.ActionContext;
import com.maidaibridge.controller.MotionPriority;
import net.minecraft.world.phys.Vec3;

public class MoveToAction extends AbstractMaidAction {
    protected double x,y,z,range,speed;private double lastDistance=Double.MAX_VALUE;private long lastProgressTick;private int recovery;
    public MoveToAction(String requestId,String type,int timeout,double x,double y,double z,double range,double speed){super(requestId,type,timeout);this.x=x;this.y=y;this.z=z;this.range=Math.max(.25,range);this.speed=Math.max(.1,Math.min(1.5,speed));}
    @Override protected void onStart(ActionContext ctx){
        if(ctx.motion().acquire(id,MotionPriority.TASK,ctx.gameTick()).isEmpty()){fail("MOTION_BUSY");return;}
        lastDistance=distance(ctx);lastProgressTick=ctx.gameTick();requestPath(ctx);
    }
    protected double distance(ActionContext ctx){return ctx.maid().position().distanceTo(new Vec3(x,y,z));}
    protected void requestPath(ActionContext ctx){if(!ctx.motion().moveTo(id,ctx.maid(),x,y,z,speed)&&recovery>=2)fail("PATH_NOT_FOUND");}
    @Override protected void onTick(ActionContext ctx){
        if(state!=com.maidaibridge.action.ActionState.RUNNING)return;double distance=distance(ctx);
        if(distance<=range){data.addProperty("remaining_distance",distance);succeed("ARRIVED");ctx.motion().release(id,ctx.maid());return;}
        if(lastDistance-distance>=.4){lastDistance=distance;lastProgressTick=ctx.gameTick();recovery=0;}
        if(ctx.gameTick()-lastProgressTick>=40){
            recovery++;lastProgressTick=ctx.gameTick();lastDistance=distance;
            if(recovery==1)requestPath(ctx);
            else if(recovery==2){double nudgeX=ctx.maid().getX()+(x-ctx.maid().getX())/Math.max(distance,1);double nudgeZ=ctx.maid().getZ()+(z-ctx.maid().getZ())/Math.max(distance,1);ctx.motion().moveTo(id,ctx.maid(),nudgeX,ctx.maid().getY(),nudgeZ,speed);}
            else {fail("STUCK");ctx.motion().release(id,ctx.maid());}
        } else if(!ctx.maid().getNavigation().isInProgress()&&ctx.gameTick()%20==0)requestPath(ctx);
    }
    @Override protected void onStop(ActionContext ctx){ctx.motion().release(id,ctx.maid());}
}
