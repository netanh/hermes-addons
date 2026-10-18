# Golden Rules for Portable Hermes Skills

Use this checklist when adding, changing, reviewing, or publishing a skill in this repository. It captures lessons from preparing the first two monitors so future contributors do not repeat packaging or operational-documentation mistakes.

These rules align with the [Hermes Skills System documentation](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills), especially **SKILL.md Format**, **Skill Directory Structure**, **GitHub installs**, and **External Skill Directories**.

## 1. Package a skill for Hermes import—not only for a source checkout

- Give each independently installable skill one clear, self-explanatory slug and its own `skills/<slug>/SKILL.md`.
- Include a SemVer `version` and useful `metadata.hermes.tags` in frontmatter; start new skills at `0.1.0` and bump the version deliberately as behavior changes. Add `platforms` only when the skill genuinely should be hidden on unsupported systems.
- Keep runtime helpers and examples inside that skill directory, e.g. `scripts/`, `examples/`, `references/`, or `templates/`.
- Consumers install a published skill with `hermes skills install <owner>/<repo>/skills/<slug>`. Hermes places it in the active profile's `$HERMES_HOME/skills/<slug>/` and downloads `SKILL.md` plus referenced support files. A consumer must **not** need to clone the repo to run the installed skill; cloning is for maintainers developing or testing the source.
- Link support files explicitly from `SKILL.md` using repository-relative Markdown links (for example, `[monitor script](scripts/monitor.py)`). Hermes does not copy arbitrary, unreferenced repository files into the installed bundle.
- Do not confuse the source repo's `skills/<slug>/` with the installed profile's `$HERMES_HOME/skills/<slug>/`. Runtime examples and wrappers must point at the installed profile copy.

## 2. Make `SKILL.md` a downstream agent's operating manual

A new agent importing the skill must be able to use it without the author's local install, source checkout, or chat history. Document the actual day-to-day workflow, not just first-time installation:

- prerequisites and the Hermes install command;
- configuration schema, defaults, validation, and a representative sanitized example;
- first-run initialization/baselining and safe ways to add or reset monitored items;
- routine check/update commands using the **installed** skill path;
- state-file schema and lifecycle, including how errors affect state;
- sample output, no-change behavior, stdout/stderr distinctions, exit codes, and retry/error policy;
- optional scheduler instructions, including wrapper placement, which installed script it invokes, delivery behavior, and concurrency/duplicate-job precautions;
- verification steps and known limits.

Do not compress away operational details just to keep the file short. Move only genuinely deep reference material elsewhere; keep the direct lifecycle instructions in `SKILL.md`.

## 3. Keep runtime data local to the installed skill, but never publish it

**Repository convention:** keep runtime files directly inside the installed skill directory (`$HERMES_HOME/skills/<slug>/`) so the code, config, state, and logs are together for permissions and backups.

- Track only sanitized `examples/config.json` in Git. The consumer copies it to an untracked `config.json` beside `SKILL.md` and `scripts/`.
- Store mutable `state.json`, `errors.log`, and any local config beside the installed skill; never commit live monitored targets, history, logs, credentials, tokens, personal paths, or notification destinations.
- Add specific `.gitignore` rules so root-level runtime config/state/log files are ignored without ignoring tracked files under `examples/`.
- Back up the complete installed skill directory before a Hub reinstall/update, then verify the local runtime files remain present. Do not assume an update preserves unbundled local files without checking.
- Do not put credentials in skill configs or outputs. Public APIs should remain unauthenticated where possible; document secure local setup if credentials are genuinely needed.

## 4. Schedule against the installed copy

Hermes cronjob's `script` field accepts a bare filename resolved under `$HERMES_HOME/scripts/`; it does not accept an arbitrary repository path.

- Put a user-specific wrapper in `$HERMES_HOME/scripts/<slug>.sh` and have it invoke `$HERMES_HOME/skills/<slug>/scripts/...`.
- Keep runtime config/state/log paths beside that installed skill, following Rule 3.
- For `no_agent=true`, require the checker to emit alert/error text to stdout when there is something to deliver and remain silent when there is no event. Explain that stdout is delivered verbatim; distinguish operational API errors from process failures delivered to `failure_deliver`.
- Initialize baselines before scheduling. Check for an existing job before creating one and prevent overlapping runs against the same state file.
- Show generic destinations as placeholders such as `telegram:<chat_id>`—never publish a real user's target.

## 5. Make behavior claims testable

- Treat source documentation as claims to verify, not proof that current code behaves as described.
- Add deterministic tests with mocked HTTP/API calls and temporary config/state/log paths. Cover initialization, no-change output, detected changes, filters, error persistence/clearance, malformed inputs, failures, and bursts of multiple changes between polls.
- If the promise is to report every change since the previous poll, do not compare only one “latest” item or trust an undocumented API ordering. Paginate the complete inventory and diff it against a saved snapshot (or explicitly document a bounded window and surface incomplete scans). Explain API-call cost and what polling cannot detect, such as items created and deleted between polls.
- Use unique temporary files and atomic replacement for state writes; document whether overlapping runs are supported. If not, state the single-writer rule explicitly.
- Verify the package from a clean/relocated checkout: no hardcoded machine paths, missing helper files, personal config/state/logs, or scheduler destinations.
- Run both skill test suites, syntax/CLI checks, documentation/link checks, and inspect `git status` before reporting readiness.

## 6. Separate preparation from publication

Preparing a local skill repo does not authorize committing, pushing, opening a PR, or publishing a release. Keep the work uncommitted unless publication is explicitly requested. Before any authorized push, review the exact staged files for secrets, runtime data, and unintended changes.
