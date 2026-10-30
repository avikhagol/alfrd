# Existing recovery paths

Source: [orchestration](../../../docs/orchestration.md), “How a request runs”, “Recovery”, “Fallback on limits”, “Activity and graph”, “Bounds”. These are existing mechanisms, not a new typed retry policy.

| Observation | Existing mechanism / response |
|---|---|
| Invalid initial plan | One repair call with validation errors; correct the envelope. |
| Invalid result / failed status | Failed attempt, retried up to max_retries with failure context; diagnose before repeating. |
| Missing expected output / refused move | Failed attempt and bounded retry; write frozen path or give valid move. |
| Missing input decision / denied access | Actionable blocker routed to orchestrator/user; exit for access relaunch. |
| Failed provider limit attempt | Configured fallback, if available; no retry spent and each agent tried once per row. |
| Dead runner | Reconcile/recovery re-adopts running commands and collects missed results; inspect before launching duplicates; preserve pause/cancel. |
| Request cost/time exceeded | ALFRD stops work with budget.exceeded; respect the bound. |

Fallback detects usage/rate/quota/429/overload/credit/model availability limits from replies or the last 64 KiB of unit logs. Orchestrator rows fall back too; exit-0 short limit-only orchestrator replies can trigger fallback, while exit-0 specialist replies are not second-guessed this way. Do not manually cycle agents.

GET …/chat/sessions/<sid>/activity?lines=60 gives compact running-unit tails. Retrieve only needed transcript pages (at most 256 KiB). A long-running operation alone is not a failure. Scheduling/retries are code-driven; the orchestrator is not a polling service. Configuration changes require the task's existing authorization.
