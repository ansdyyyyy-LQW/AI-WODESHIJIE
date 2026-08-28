package com.maidaibridge.action.impl;
import com.maidaibridge.action.AbstractMaidAction;import com.maidaibridge.action.ActionContext;import com.maidaibridge.controller.MotionPriority;import net.minecraft.world.entity.Entity;import java.util.UUID;
public final class FollowEntityAction extends AbstractMaidAction {private final UUID targetId;private final double range;private Entity target;public FollowEntityAction(String req,int timeout,UUID targetId,double range){super(req,"follow_entity",timeout);this.targetId=targetId;this.range=range;}
@Override protected void onStart(ActionContext ctx){target=ctx.level().getEntity(targetId);if(target==null){fail("TARGET_GONE");return;}if(ctx.motion().acquire(id,MotionPriority.TASK,ctx.gameTick()).isEmpty())fail("MOTION_BUSY");}
@Override protected void onTick(ActionContext ctx){if(target==null||!target.isAlive()){fail("TARGET_GONE");return;}if(ctx.maid().distanceToSqr(target)<=range*range){succeed("IN_RANGE");ctx.motion().release(id,ctx.maid());return;}ctx.motion().moveTo(id,ctx.maid(),target,.9);}
@Override protected void onStop(ActionContext ctx){ctx.motion().release(id,ctx.maid());}}
