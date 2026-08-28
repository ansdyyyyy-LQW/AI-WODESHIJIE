# Maid AI R&D Runner

这个模块只在独立 worktree 中运行五日研发任务。它不会修改正在运行的生产源码，也不会复制文件到 Minecraft `mods`。

主要命令：

```text
maid-rnd prepare --repo <repo> --worktree-root <dir> --cycle-id cycle-001
maid-rnd run --repo <repo> --worktree-root <dir> --cycle-id cycle-001 --input <input> --output <handoff> --harness-command "..."
maid-rnd validate --output <handoff> --update-checksums
```

没有配置外部 Harness 时，`run` 只准备隔离目录并返回 `READY`，不会伪造研发完成。
