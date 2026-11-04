---
name: blocker-reporting
description: Report an ALFRD task blocked on input, permission or another decision with precise needs, required access and mailbox continuation IDs.
---

# Report a blocker

- Explain what prevents progress, the evidence and the smallest decision needed. Classify kind as input, permission or other; give a clear question and useful options when applicable.
- Use needs: user for a person/access decision, orchestrator for a planner decision. For denied folders/commands use kind: permission and requires naming exact absolute folders and/or command patterns. Exit blocked so ALFRD can ask and relaunch; do not repeat denied access in the current session.
- Ask through the mailbox when appropriate and preserve its full mailbox_message_id on a stopped blocker. Read/ack is not approval, and exact-action approval cannot expand the running sandbox.
- End with a fenced alfrd.orchestration/1 result, status blocked, a useful summary and at least one blocker. List partial deliverables in artifacts and explain remaining work.

Read [blocker fields and decision boundaries](references/contract.md) when composing the report.
