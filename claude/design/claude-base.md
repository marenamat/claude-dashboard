# Design goals for the base repository

User: Remove this file when templating.

Claude: Ignore this file if left in a repository not called `claude-base`.

## Self-improvements

Look into your own setup and think whether something can be made more
efficient. Check projects forking or elsehow using this setup, look for changes
to incorporate, and suggest updates.

## Documentation

Document everything needed to fork it and start a new Claude-assisted project from scratch.

## Website (GitHub Pages)

A static website lives in `docs/` and is deployed via GitHub Pages.
It covers:

- What claude-base is and why it exists
- How the automated loop works (clanker-run → clanker-prep → Claude)
- Step-by-step setup guide for new forks
- Requirements (GitHub token, Claude API key, cron)

The website follows the standard frontend stack: pre-generated HTML 5,
dedicated CSS, lightweight and responsive. No WASM needed for pure docs.

## Template repository

The repo is marked as a GitHub template so users can "Use this template"
directly from the GitHub UI.
