---
name: verification-checklist
description: Verify an ALFRD task before claiming done by checking acceptance criteria, expected outputs, artifact provenance and evidence.
---

# Verify completion

- Map each assigned acceptance criterion to a concrete test or inspection. Record commands, outcomes and verification limits in the summary or a report artifact.
- Inspect every frozen expected output at its exact absolute path, including type and meaningful contents. ALFRD checks existence; acceptance checks establish correctness.
- Verify input hashes and current output paths using [artifact-handoff](../artifact-handoff/SKILL.md).
- For high-risk work, use an independent check when authorized and within task/delegation bounds, or state the verification limit. This is a review practice, not an automatic scheduler stage.
- Read compact mailbox mail before finalizing. Claim done only when the checks support it; report unresolved failures or actionable blockers honestly.
- Use existing result fields; put evidence in summary or artifacts rather than inventing evidence/checks fields.

Read [completion mechanics](references/contract.md) when preparing the result.

## Efficient test verification

- Read the repository's test instructions first. Use its interpreter and working directory; do not fall back automatically to a system pytest after any failure.
- Check which suites include other suites. In ALFRD, full pytest already runs all Studio Node tests; a separate full JS run repeats that coverage. Browser acceptance requires the configured Playwright/Chromium environment, not optional dependency skips.
- Use targeted tests while fixing failures and one final complete verification after changes. Preserve full logs and report durations; do not rerun a full suite merely to recover truncated output.
- Use a bounded runner and wait on its process/session handle. Never poll a broad command-text match (a pgrep loop can match itself). On timeout, inspect the last test and clean up the owned process tree.
- For ALFRD commands and bounded concurrency, see docs/studio-guide.md, Tests.
