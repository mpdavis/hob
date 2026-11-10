# CLAUDE.md

Guidance for coding agents in this repository. What Hob does is in `README.md`;
why it is shaped this way is `docs/sre-agent.md` in
[mpdavis/homelab](https://github.com/mpdavis/homelab), which also holds the
stack that runs it (`docker/infra/hob/`).

## Release flow

1. Merge to `main` → `build.yml` publishes `ghcr.io/mpdavis/hob:1.0.<run_number>`
   (only when the image's build context changed; PRs build but never push).
2. Renovate in homelab sees the new tag and opens the pin bump there.

An image change and its pin bump can never land in one PR: the tag does not
exist until main publishes it.

## Checks

- `Tests / test` is the required check on `main`. It has no `paths` filter on
  purpose; a required check that gets skipped blocks the PR forever.
- Run the suite on the Python the image ships, not whatever is local — see
  `README.md`.

## Rules

- A failed fetch from Grafana must never be treated as "no alerts". An empty
  snapshot resolves every open thread.
- Hob holds its own credentials only. Nothing here may need a human's token.
- Comments explain why, never what. Same rule as homelab's `AGENTS.md`.
