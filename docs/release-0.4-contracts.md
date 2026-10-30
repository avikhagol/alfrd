Frozen for 0.4.0.0: this contract is copied from final_plan.md §3 (sha256 dabd233c3a27a827d8bd4c21b5c76e8bdc49bae7ec56c6316052db334112bddb) and must not change during 0.4.0.0.

## 3. Shared contracts (frozen by F0)

Let `C = /api/studio/projects/<p>/chat`, `S = C/sessions/<s>`, `R = S/requests/<r>`. All mutations keep the existing CSRF, origin, local-access and category guards. Errors use `{error, code, field?}` with these statuses: 400 invalid, 403 policy, 404 missing, 409 stale or conflict, 413 too large, 415 unsupported type, 422 unsupported adapter capability. Browser-only mode never authorises a write.

```ts
type Scope = {project: string; session: string; request: string|null};
type AgentCapabilities = {images: {supported: boolean; mime_types: string[]; reason?: string};
                          saved_skills: boolean; native_skill_toggle: boolean; reason?: string};
type Count = {value: number|null; coverage: 'complete'|'partial'|'unavailable'; reason?: string};
type TaskAction = {id: 'log'|'prompt'|'reply'|'files'|'retry'|'cancel'|'copy_id'; label: string; enabled: boolean; reason?: string};
```

| Surface | Contract |
|---|---|
| Live state | `GET S/state?since_rev=N` → `{rev, unchanged:true}` or the full state with `rev`. The client drops any response whose `rev` is lower than the last one it applied. `rev` is bumped only under `Session.lock()` and written atomically |
| Probe | `GET C/probe` gains per-agent `AgentCapabilities`, upload limits and feature-availability reasons |
| Attachments | `POST S/attachments` (multipart `file`) → 201 `{ref, sha256, media_type, bytes, width, height, name}`. `GET S/attachments/<ref>/content`. `POST S/messages {text, attachments?: ref[], client_message_id?}`; text may be empty if at least one image is attached |
| Task actions | `GET S/tasks/<row>/retry-preview`. Retry and cancel accept `expected_status` and `expected_unit`, and return 409 `{error, current_status, current_unit}` on a mismatch |
| Overview | `GET R/overview → {rev, goal:{text, source}, criteria:[{id, text, task_rows, status:'unverified'\|'verified'\|'failed'}], files:[{path, ref, producer_rows, state:'expected'\|'recorded'\|'missing'\|'changed', media_type?, bytes?}]}`. `GET S/artifacts/raw?ref=` |
| Settings | `GET /studio/ui-flags → {revision, flags:[{key, scope:'browser'\|'project'\|'machine', type, default, value, effective, locked, disabled, reason?, dangerous, label, help, requires}]}`. Saves go to the existing route for each scope, with `expected_revision` |
| Mailbox v2 | Adds `code?`, `category?`, `priority?`, `summary?`, `description?` and `step_id?`. `state` is derived and gains `acked`. The handle is `#seq`. Each record must stay within 32 KiB |
| Steps | `OrchestrationStep {id, request_id, kind:'plan'\|'delegate'\|'execute'\|'repair'\|'replan'\|'summary', task_row?, unit?, attempt, status, started_at?, finished_at?, waiting_on:[msg_id]}`, replayed from the plan event log |
| Skills | `GET C/skills → {skills:[{id, name, description, source, sha256, bytes}], agents:{<a>:{adapter, native:{skills, plugins, mcp}, effective_args, notes}}}`. `skill_selection:{enabled, selected_ids}` per project, optionally per agent, snapshotted per request |
| Telemetry | Sidecar `alfrd.telemetry/1` per unit log: `{parser_version, inode, size, offset, counts, unknown, oversize_lines, coverage}` |

Mailbox type codes (from `mailbox_codes.py`; `alfrd mailbox codes` prints them):

| Code | Kind | Category | Default priority | Meaning |
|---|---|---|---|---|
| PRG | info | progress | low | Step progress `{step, of, label}` |
| STR | info | progress | low | Work started |
| DON | info | result | normal | Main work finished |
| ART | info | result | normal | Output file ready `{path}` |
| NTE | info | note | low | FYI |
| WRN | info | error | high | Degraded, continuing |
| ERR | info | error | high | A step failed `{cause?}` |
| BLK | info | blocker | urgent | Cannot proceed `{needs?}` |
| DEP | info | coordination | normal | Note to a sibling or dependent task |
| CLM | info | coordination | high | Claims a shared path `{path}` |
| ASK | question | question | high | Question for the user |
| ASO | question | question | high | Question for the orchestrator |
| PRM | permission_request | permission | urgent | Approval for an exact action |
| REP | reply | (inherited) | (inherited) | Set by the store |

Compact prompt line: `#seq CODE priority from→to state "summary"`. A message body is included only for unresolved messages addressed to the current task, up to 2 KB in total. The section is at most 600 characters with no messages and at most 2.5 KB with 20.
