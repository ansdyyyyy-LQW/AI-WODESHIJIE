package com.maidaibridge.compat.tlm;

import com.github.tartaricacid.touhoulittlemaid.api.ILittleMaid;
import com.github.tartaricacid.touhoulittlemaid.api.LittleMaidExtension;
import com.github.tartaricacid.touhoulittlemaid.ai.agent.context.GameContextRegister;
import com.github.tartaricacid.touhoulittlemaid.ai.agent.tool.ToolRegister;
import com.github.tartaricacid.touhoulittlemaid.entity.task.TaskManager;
import com.maidaibridge.task.MaidAutonomousTask;

@LittleMaidExtension
public final class MaidAiTlmExtension implements ILittleMaid {
    @Override public void addMaidTask(TaskManager manager) { manager.add(new MaidAutonomousTask()); }
    @Override public void registerAIMaidContext(GameContextRegister register) { }
    @Override public void registerAITool(ToolRegister register) { }
}
