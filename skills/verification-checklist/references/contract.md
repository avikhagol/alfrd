# Completion mechanics

Sources: [orchestration](../../../docs/orchestration.md), “How a request runs” and “Protocol”; [chat.py](../../../src/alfrd/chat.py), MAILBOX_CONTRACT.

End with one fenced JSON block: protocol alfrd.orchestration/1, kind result, result containing status (done, failed or blocked) and a nonempty summary. Optional result fields are task_id, artifacts, usage, blockers, tasks and outputs_changed; consult src/alfrd/schemas/orchestration_result.v1.json for their shapes. Provider usage overrides agent estimates. Blocked requires at least one blocker.

An invalid envelope or failed status fails the attempt. An unstructured reply can count as done, but use the explicit envelope for protocol tasks. Frozen expected outputs must exist before completion is accepted; missing outputs fail and retry within max_retries. Existence does not establish correctness. Check current paths after valid output moves and resolve stale input hashes before claiming work based on that version.
