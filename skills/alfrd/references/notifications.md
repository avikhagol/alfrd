# Notifications (ALFRD)

The runner (not `alfrd serve`) appends every plan event to
`.alfrd/plans/<id>/events.jsonl` and delivers it through routes. Kinds:
`plan.started|finished|failed|cancelled|interrupted`, `turn.started|finished|failed|retrying|fallback_model|idle`,
`review.pending|approved|rejected`, `handoff.published`, `limit.reached`.

```yaml
notify:                    # alfrd.yaml: only `via: desktop` allowed here
  idle_after: 600          # s without output -> turn.idle; 0/false = off
  routes:
    - {via: desktop, 'on': [review.pending, plan.failed, plan.finished, turn.idle]}   # quote 'on'
```

`webhook` (`url`, `secret`, `headers`; https unless loopback) and `command`
(`argv`, message JSON on stdin) only in the user's `~/.config/alfrd/notify.json`
(`{"routes": [...]}`, applies to all projects).
`telegram` (`token`, numeric `chat_id`, optional `studio_url`; plain text, link to Studio) is user-only too; without `token`/`chat_id` it uses Settings → Plugins → Telegram. `on` takes kinds or `plan.*`, `turn.*`, `*`.
Routes are read when a runner starts: edits apply to new or restarted runners.
Events of one plan within 20 s are batched; `notify.cursor` prevents resends.
An unusable notifier (no desktop, SSH) is skipped with one warning in `runner.log`;
notifications never fail a plan.
