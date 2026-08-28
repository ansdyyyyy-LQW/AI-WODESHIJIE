package com.maidaibridge.reflex;

import com.github.tartaricacid.touhoulittlemaid.entity.passive.EntityMaid;
import com.maidaibridge.action.ActionEngine;
import com.maidaibridge.controller.MotionArbiter;
import com.maidaibridge.controller.MotionPriority;
import com.maidaibridge.observe.ThreatClassifier;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.phys.Vec3;

import java.util.Comparator;
import java.util.List;
import java.util.UUID;

public final class ReflexEngine {
    private static final UUID REFLEX_ID=UUID.nameUUIDFromBytes("maid-ai-reflex".getBytes(java.nio.charset.StandardCharsets.UTF_8));
    private final ThreatClassifier classifier=new ThreatClassifier();private String state="NONE";private long lastAttack;
    public String state(){return state;}
    public void tick(EntityMaid maid,ServerLevel level,long tick,ActionEngine actions,MotionArbiter motion){
        List<LivingEntity> hostiles=level.getEntitiesOfClass(LivingEntity.class,maid.getBoundingBox().inflate(10),e->e.isAlive()&&classifier.hostile(e,maid));
        if(maid.getAirSupply()<40&&maid.isInWater()){
            takeOver("DROWNING_ESCAPE",maid,level,tick,actions,motion);motion.moveTo(REFLEX_ID,maid,maid.getX(),maid.getY()+5,maid.getZ(),1.2);maid.getJumpControl().jump();return;
        }
        if(maid.isOnFire()){
            takeOver("FIRE_ESCAPE",maid,level,tick,actions,motion);Vec3 look=maid.getLookAngle();motion.moveTo(REFLEX_ID,maid,maid.getX()+look.x*8,maid.getY(),maid.getZ()+look.z*8,1.1);return;
        }
        if(maid.getHealth()<=Math.max(4,maid.getMaxHealth()*.25f)&&!hostiles.isEmpty()){
            takeOver("CRITICAL_HEALTH_RETREAT",maid,level,tick,actions,motion);LivingEntity nearest=hostiles.stream().min(Comparator.comparingDouble(maid::distanceToSqr)).orElseThrow();Vec3 away=maid.position().subtract(nearest.position());if(away.lengthSqr()<.01)away=new Vec3(1,0,0);Vec3 dest=maid.position().add(away.normalize().scale(12));motion.moveTo(REFLEX_ID,maid,dest.x,maid.getY(),dest.z,1.2);return;
        }
        LivingEntity melee=hostiles.stream().filter(e->maid.distanceToSqr(e)<=6.25).min(Comparator.comparingDouble(maid::distanceToSqr)).orElse(null);
        if(melee!=null&&tick-lastAttack>=12){state="IMMEDIATE_MELEE_DEFENSE";maid.getLookControl().setLookAt(melee);maid.doHurtTarget(melee);maid.swing(net.minecraft.world.InteractionHand.MAIN_HAND);lastAttack=tick;return;}
        if(!state.equals("NONE")){motion.release(REFLEX_ID,maid);state="NONE";actions.clearReflex();}
    }
    private void takeOver(String next,EntityMaid maid,ServerLevel level,long tick,ActionEngine actions,MotionArbiter motion){if(!state.equals(next)){actions.preempt(maid,level,tick,next);state=next;}motion.acquire(REFLEX_ID,MotionPriority.EMERGENCY_REFLEX,tick);}
    public void stop(EntityMaid maid,MotionArbiter motion,ActionEngine actions){motion.release(REFLEX_ID,maid);state="NONE";actions.clearReflex();}
}
