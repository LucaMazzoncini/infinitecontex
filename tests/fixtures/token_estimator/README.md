# Token-estimator calibration corpus

`corpus.json` is a deterministic, version-controlled set of small synthetic samples authored for InfiniteContext. It contains no copied third-party project code. Each record names its category and the tokenization risk it exercises.

The corpus covers C#, Unity-style C#, Python, JSON, YAML, Markdown, PowerShell and shell commands, Git diffs, stack traces/compiler diagnostics, minified text, English and Italian prose, mixed prose/code, ASCII-heavy text, accented Latin, CJK, emoji/supplementary characters, combining sequences, long identifiers, file paths, empty and whitespace-only input, and paired LF/CRLF content.

Before estimation and reference encoding, CRLF and lone CR are replaced with LF. No content is trimmed and no Unicode normalization is applied. Consequently, the `lf` and `crlf` samples must produce identical counts, while combining sequences remain distinct code points.

The reviewed reference counts are in `tests/golden/token_estimator_qwen2_5_coder.json`. They are model-family evidence, not a claim of exact tokenization for every Ollama model.
