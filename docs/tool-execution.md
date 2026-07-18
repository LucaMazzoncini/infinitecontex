# Safe Repository Read Tools

G2 supplies handlers for exactly five built-in definitions:

| Canonical name | Operation |
| --- | --- |
| `repository.list-inventory` | Page through validated inventory entries |
| `repository.read-file` | Read bounded UTF-8 text from one explicit file |
| `repository.read-source-range` | Read one explicit inclusive line range |
| `repository.search-paths` | Filter validated paths by safe glob, suffix, or prefix |
| `repository.search-literal` | Search bounded text using a literal query |

Every handler binding includes the exact registry definition fingerprint. Calls pass through the read-only gateway and an invocation-scoped grant tied to the exact snapshot, tool, normalized input, and requested scopes. There is no general grant and no handler for mutation, shell, Git-write, network, secret access, or external paths.

Paths are normalized to repository-relative POSIX form. The established inventory and resolver reject traversal, absolute/UNC/device paths, repository escape, unsafe links, ignored files, `.git`, and `.infctx`. The sensitive-path policy also denies environment-secret files, private keys, credentials, and token stores before scanning. UTF-8 and UTF-8 BOM are supported; binary, oversized, changed, or unsupported-encoding files receive explicit non-success results.

Literal search never interprets regular-expression metacharacters. Optional case folding, whole-word matching, safe path globbing, bounded context, file/match/byte limits, and deterministic offsets are supported. A partial result reports `has_more` and either a continuation offset or next line range.

Task-bound reads use `infctx plan tool-read`. They additionally require the exact current revision and task fingerprint, fresh passing context analysis and snapshot, requested repository-read capability, passing G1 structural policy, and a path within declared scopes without forbidden overlap. They do not create persistent capability grants.

Compact records in `.infctx/tool-executions/` contain hashes, counts, statuses, and linkage only. Source content, snippets, secrets, and full queries are never persisted.
