---
name: artifact-handoff
description: Consume or hand off ALFRD artifacts with exact hash refs, bounded retrieval, provenance and validated expected-output path changes.
---

# Hand off artifacts

- Copy supplied path@sha256 refs exactly. Verify the local file SHA-256 before relying on that version, or use the hash-checking artifact endpoint. Read only relevant sections/pages; avoid loading another task's full transcript.
- Write outputs at their frozen absolute paths. When a move is needed, use result outputs_changed or mailbox OUT with the output ID and new absolute writable path, informing dependents through that contract.
- List produced files in result artifacts; ALFRD records actual hashes. Do not invent hashes, claim inaccessible files were registered, or confuse output paths with hash-checked input refs.
- Record source refs, transformations and current output locations in the summary or a report when needed for downstream work.

Read [paths, refs and output moves](references/contract.md) when retrieving artifacts or moving outputs.
