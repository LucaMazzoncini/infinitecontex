# Approved Repository Text Mutations

G3 uses a strict offline sequence:

```powershell
infctx repo patch validate --file mutation.json --repo .
infctx plan mutation propose PLAN_ID --task TASK_ID --file mutation.json --repo .
infctx plan mutation approve PLAN_ID PROPOSAL_ID --actor "User" --reason "Reviewed"
infctx plan mutation apply PLAN_ID PROPOSAL_ID --repo .
infctx tool mutation list
```

The mutation JSON schema version is `1`. `replace_line_range` requires an exact path, deterministic file kind, preimage SHA-256, inclusive line range, exact old text, replacement text, and expected postimage SHA-256. `create_text_file` requires a path that does not exist, complete UTF-8 content, declared kind, and postimage SHA-256.

One proposal uses one exact tool/file kind and at most one operation per target. Limits are 16 targets, 2 MiB per file, 8 MiB aggregate preimages/postimages, and a 128 KiB persisted preview. Preview abbreviation is explicit and never omits structured JSON operations.

Proposal and approval commands do not edit targets. Apply requires the exact current plan/task/context/snapshot, requested capability, affected scope, non-forbidden path, exact approval, preimages, and postimages. Each target replace/create is atomic; multi-file failure triggers verified reverse rollback. No security bypass exists.

Minimal replacement request:

```json
{
  "schema_version": 1,
  "operations": [{
    "operation": "replace_line_range",
    "path": "src/example.py",
    "file_kind": "source",
    "expected_preimage_sha256": "<sha256>",
    "start_line": 1,
    "end_line": 1,
    "expected_old_text": "old\n",
    "replacement_text": "new\n",
    "expected_postimage_sha256": "<sha256>"
  }]
}
```

Proposal output includes the exact proposal fingerprint, unified preview, target hashes, and counts. Approval binds that fingerprint. Apply returns the resulting hashes and a compact mutation record; neither compact approval nor execution records store source text or the diff.
