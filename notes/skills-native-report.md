# Provider-native skills wiring

Implemented in `/mnt/6438D98627D1388F/Intelligence/gh/alfrd`; changes remain uncommitted.
Input `notes/chat-orchestration-review.md` SHA-256 verified as
`4489aaf34c72d70f2850c7857428d2f6ecfc8f9292cd17e8ee2173f73ef531ab`.

## Behavior and precedence

- Claude lean startup now defaults only to `mcp: none` and `plugins: false`. Skills remain
  enabled, whether or not saved skills are selected. Explicit `skills: false` adds
  `--disable-slash-commands`, including when saved skills are selected; `skills: true`
  leaves them enabled. Explicit CLI startup flags retain their existing precedence.
- Selected saved skills remain frozen per request and injected for every provider. Each has
  the deterministic directive `Use skill <id> for <description or name>; full text in <source>`.
  The directive says the injected snapshot takes precedence if the source changes.
- Codex chat launches expose the request's selected skills through exclusive directory symlinks:
  `<actual launch cwd>/.agents/skills/alfrd-<id>` → the original skill directory. This includes
  references and scripts. Launches use the compiled entrypoint/step `skills` setting;
  explicit false skips exposure. Promoted workflows also retain explicit skills/plugins/MCP options.
- Existing files, directories, and mismatched symlinks are never overwritten or deleted.
  Exact matching symlinks are reusable. Symlinked destination roots, source-manifest changes,
  collisions, and write failures retain injection as fallback. `unit.skill_delivery` records
  `{id, delivery: "native" | "injected", path: string | null, reason: string | null}` per skill.
- No home directories or provider config files are changed. Claude natively discovers
  `.claude/skills`; ALFRD's other saved-skill roots use injection for Claude.

## Codex evidence and limits

Installed CLI: `codex-cli 0.162.1` (`codex --version`). The
[official Codex skills documentation](https://learn.chatgpt.com/docs/build-skills), checked
2026-10-29, documents `.agents/skills` from launch CWD through the repository root,
`~/.agents/skills`, `/etc/codex/skills`, and bundled system skills. It explicitly supports symlinked
skill directories. ALFRD uses the documented CWD root; it does not rely on legacy
`~/.codex/skills` discovery. The Agents API capability-directories mechanism is not a CLI flag.

Native exposure is currently wired into chat scheduler launches, where request selections and
snapshots exist. Plain workflow launches have no request selection to expose. Native discovery
is best effort; injection is always retained. Source manifests need valid provider skill metadata
for Codex to load them. References/scripts are live source files, not request snapshots. Links
remain for reuse and can remain discoverable on subsequent launches in the same workspace;
use isolated run workspaces for strict run scoping. `skills: false` skips new Codex exposure,
not the CLI's independently installed skills. It disables native Claude skills via its supported flag.

## Read-only inventory API

Backend: `alfrd.skills.skill_inventory(project, selection=None)`.
Route: `GET /api/studio/projects/<project>/chat/skills/inventory`.
No install, process launch, provider call, content mutation, or native-link creation occurs.
POST is rejected with HTTP 405. Existing `/skills` and `/skills/preview` response shapes stay intact.

The response is an object with a `skills` array, in deterministic discovery order. Example:

```json
{
  "skills": [
    {
      "id": "review",
      "name": "Review",
      "description": "check diffs",
      "source": ".claude/skills/review/SKILL.md",
      "source_root": ".claude/skills",
      "path": "/workspace/.claude/skills/review/SKILL.md",
      "bytes": 72,
      "sha256": "<64 lowercase hexadecimal characters>",
      "reference_files": [".claude/skills/review/references/guide.md"],
      "selected_by_default": true,
      "providers": {
        "claude": {"delivery": "native"},
        "codex": {"delivery": "native", "fallback": "injected"},
        "generic": {"delivery": "injected"}
      }
    }
  ]
}
```

Field meanings:

- `id`: directory ID, first occurrence wins across `.alfrd/skills`, `.claude/skills`, `skills`.
- `name`, `description`: existing frontmatter metadata, with ID fallback for missing name.
- `source_root`, `source`: project-relative POSIX root and manifest path; `path`: absolute manifest path.
- `bytes`, `sha256`: actual manifest byte size and SHA-256, not reference-directory totals.
- `reference_files`: sorted project-relative regular files recursively under `references/`;
  symlinks and paths outside the skill directory are excluded. No reference text is returned.
- `selected_by_default`: project chat selection is enabled and this ID is selected. This does not
  mean all providers auto-activate the skill.
- `providers`: delivery policy for a selected skill with native skills enabled, not live launch
  state. Claude is native only for `.claude/skills`; other saved roots are injected. Codex is
  eligible for native exposure with injection fallback. All selected manifests also retain prompt
  injection. Explicit native-disable settings and launch failures can override this policy;
  consult the actual launch unit's `skill_delivery` for Codex outcomes.

Discovery inherits the existing 64 KiB per-manifest limit, safe-root checks, symlink exclusions,
and duplicate-ID precedence. Selection retains its 128 KiB total snapshot cap.

## Validation

- `pytest tests/test_skills.py tests/test_skills_k4.py tests/test_agent_controls.py tests/test_chat*.py -q`:
  263 passed; three existing local HTTP fixtures errored because the sandbox disallowed socket
  creation. Reran exactly those three tests with permitted local socket access: 3 passed.
- Two new scheduler-level native-exposure tests (added while the suite was running): 2 passed.
  Together these cover all 268 current tests in that selection, including Claude lean/default/true/false
  precedence, source edits/retries, no overwrite, symlink-root rejection, endpoint metadata and GET-only behavior.
- `pytest tests/test_agent_loop.py tests/test_loop_features.py tests/test_ui_flags.py -q`:
  124 passed.
- Focused directive tests after a lint-only parentheses adjustment: 3 passed.
- `ruff check --select E9,F63,F7,F82` over all changed Python/test files: passed.
  Full strict Ruff reports existing repository style/import/fixture warnings; a newly introduced
  implicit-concatenation warning was corrected. No unrelated lint cleanup was performed.
- `git diff --check`: passed. No changes committed.

No `skills/` content or web JavaScript was edited for this task. Other concurrent work has
modified/added skill-library files in the shared checkout; those changes were left untouched.
