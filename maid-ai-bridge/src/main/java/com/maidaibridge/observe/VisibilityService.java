package com.maidaibridge.observe;

import com.github.tartaricacid.touhoulittlemaid.entity.passive.EntityMaid;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.level.ClipContext;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;

public final class VisibilityService {
    public boolean isExposed(ServerLevel level, BlockPos pos) {
        for (Direction direction : Direction.values()) {
            BlockState neighbor = level.getBlockState(pos.relative(direction));
            if (neighbor.isAir() || !neighbor.getFluidState().isEmpty()) return true;
        }
        return false;
    }
    public boolean canSee(EntityMaid maid, ServerLevel level, BlockPos pos) {
        Vec3 start=maid.getEyePosition();Vec3 end=Vec3.atCenterOf(pos);
        BlockHitResult hit=level.clip(new ClipContext(start,end,ClipContext.Block.COLLIDER,ClipContext.Fluid.NONE,maid));
        return hit.getType()== HitResult.Type.MISS || hit.getBlockPos().equals(pos);
    }
}
