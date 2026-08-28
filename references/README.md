# Reference Sources

`REFERENCE_LOCK.json` 固定了本次实现研究时使用的上游版本。正式源码包不直接打包这些完整仓库，避免无必要地扩大交付体积和混入不同许可代码。

开发者可以执行：

```text
python tools/fetch_references.py
```

脚本会克隆到 `references/clones/` 并检出锁定 commit。该目录被 `.gitignore` 排除，不进入正式产品。
