# Paths and references

Sources: [orchestration](../../../docs/orchestration.md), “Artifact references”, “Expected outputs are frozen”, “An agent can move an output”; [agent-mailbox.md](../../../docs/agent-mailbox.md), OUT code.

A ref <project-relative path>@<sha256 hex> identifies a version. GET …/chat/sessions/<sid>/artifacts?ref=…&offset=…&length=… resolves only inside project root after symlinks; absolute paths, traversal and links out are refused. Hash mismatch returns 409; each read is at most 256 KiB. Preserve exact refs and page by offset/length. On mismatch, report stale input and obtain the intended version/ref rather than silently accepting changed content.

Expected outputs may be outside the project if writable. That does not make them eligible for the project-confined artifact endpoint; distinguish filesystem deliverables from retrievable refs. Results accept paths, refs or {path, sha256?, description?}; ALFRD hashes files itself. Verify recorded refs before downstream use.

Use outputs_changed: [{output: "o1", to: "/abs/new/path", reason: "..."}] only for actual moves, or send OUT {output, to, from?, reason?} as soon as known. Applied with the result: result wins for the same output, otherwise last mail that attempt sent wins. ALFRD validates all moves or refuses all and retries the attempt.

Destinations must be absolute and writable, not the project/writable folder itself, not the output's current path or another output's final path. Move each output once per result; swapping two paths is allowed. Valid moves increment revision/history; dependents and summaries see current paths. A trailing / denotes a folder; missing current outputs fail completion. Output IDs o1, etc. identify frozen outputs, not artifact-list positions.
