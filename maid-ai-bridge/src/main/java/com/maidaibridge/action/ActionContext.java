package com.maidaibridge.action;

import com.github.tartaricacid.touhoulittlemaid.entity.passive.EntityMaid;
import com.maidaibridge.controller.MotionArbiter;
import com.maidaibridge.inventory.MaidInventoryService;
import com.maidaibridge.observe.VisibilityService;
import net.minecraft.server.level.ServerLevel;

public record ActionContext(EntityMaid maid, ServerLevel level, long gameTick, MotionArbiter motion,
                            MaidInventoryService inventory, VisibilityService visibility) { }
