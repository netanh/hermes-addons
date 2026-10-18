# Hermes Add-ons

A collection of independently installable Hermes Agent skills and utilities.

**Contributors and agents:** read [Golden Rules for Portable Hermes Skills](docs/golden-rules-for-portable-skills.md) before adding, modifying, or reviewing a skill. It defines how skills are installed into Hermes, where runtime data belongs, what `SKILL.md` must teach, and how to verify before publication.

## Skills

- [`docker-hub-tag-monitor`](skills/docker-hub-tag-monitor/) — monitor Docker Hub images for new tags.
- [`github-release-monitor`](skills/github-release-monitor/) — monitor GitHub repositories for new releases and tags.

Each skill is self-contained under `skills/<skill-name>/` and includes its own `SKILL.md`, helper scripts, example configuration, and tests. Start with that skill's `SKILL.md` for prerequisites, setup, usage, state management, and verification.

## Safety and portability

Example configuration files contain placeholders/examples only. Runtime configuration, state, logs, credentials, and scheduler destinations belong to the user's local environment and must not be committed. The monitors use public APIs and do not require credentials for basic operation.

## License

MIT; see [LICENSE](LICENSE).
