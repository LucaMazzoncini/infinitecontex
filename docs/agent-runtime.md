# Supervised local agent runtime

The G8 runtime is an offline, bounded consumer of an exact G7 grant. It never derives permission from task status, requested capabilities, evidence, or model text.

Typical workflow:

```text
infctx agent policy
infctx plan agent create PLAN_ID --task TASK_ID --grant GRANT_ID --session SESSION_ID --model-profile PROFILE_ID
infctx plan agent show PLAN_ID RUN_ID
infctx plan agent run PLAN_ID RUN_ID --repo PATH
infctx plan agent-step list PLAN_ID RUN_ID
infctx plan agent-mutation show PLAN_ID RUN_ID
infctx plan agent-mutation export PLAN_ID RUN_ID --output mutation.json
```

`create`, `show`, and list operations never contact Ollama. Only `agent run` does. A candidate export is not an approval and does not modify the repository. Import it through the normal G3 proposal workflow, review the deterministic diff, and obtain a separate human approval.

Default hard ceilings are 24 model calls, 20 successful actions, three invalid responses, 2 MiB of returned tool data, 32,000 aggregate output tokens, 30 minutes, and one mutation candidate. A narrower G7 grant always wins.
