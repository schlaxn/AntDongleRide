# Working rules for this repo

## Git
- Commit and push directly to `main`. Never create branches (no feature branches, no pull requests unless the user asks).
- Commit and push only when the user asks for it.
- Run the relevant unit tests before every commit.
- Stage files by name. Do not use `git add -A` or `git add .`.
- Do not commit the user's uncommitted work (for example `static/row/world.js`, `.gitignore`, `static/zoo*`, `tools/`, `static/shared/`) unless the user names it. If a file mixes the user's changes with ours, ask before committing it.
- Never commit private data: `recordings/` and `rower/measurements/` are personal and must stay out of git.
- Commit messages end with the `Co-Authored-By` line from the current session instructions.

## Rowing app
- The rower is never asked to type values while rowing. Calibration detection is automatic, and the rower is assumed to hold the demanded speed.
- `ROWER_HANDOVER.md` is the source of truth for the rowing work. Update it when findings change.
