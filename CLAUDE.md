# ObsidianLLMWIKI — Claude Code

@AGENTS.md

The imported Agent Contract defines repository/memory/official-source routing,
retrieval commands and write guards for both Claude Code and other coding agents.

## Disabled Features

- **Plan mode disabled.** Never use `EnterPlanMode` in this project — it causes hangs. Start implementing directly.

## Architecture

- All vault paths come from `config.toml` (git-ignored, never commit)
- Global skill registered at `~/.claude/skills/obsidian-llmwiki/` by `install.sh`
- References in the global skill are symlinks to repo files — `git pull` auto-updates them
- No personal data (notes, .docx, vault paths) should ever be committed
