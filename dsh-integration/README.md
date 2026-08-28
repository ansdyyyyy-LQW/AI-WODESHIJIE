# MaidAI DeepSeek Harness integration

This directory is a thin, pinned integration around official
`@deepseek-ai/dsh@0.1.1-rc.2`. It does not fork or replace DeepSeek Harness.

The `maidai` profile loads the official base bundle, keeps the Windows sandbox
in `workspace-write`, and mounts one JSONL driver. The driver uses the official
Agent Registry to create or resume the cycle's persistent session, runs each
R&D phase in the same workspace, flushes the official session store, and emits
machine-readable status, tool activity, usage, result, and error messages.

Production launch:

```text
node lib/launcher.js
```

Required environment:

- `DSH_HOME`: MaidAI's private DSH home.
- `DEEPSEEK_API_KEY`: a short-lived credential supplied by MaidAI. In formal
  operation this is the local budget-proxy token, never the user's real key.
- `DEEPSEEK_BASE_URL`: the local R&D budget proxy endpoint.

The process working directory is the only writable R&D source workspace.
