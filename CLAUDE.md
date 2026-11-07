# Working rules for this repo

## Git
- Commit and push directly to `main`. Never create branches (no feature branches, no pull requests unless the user asks).
- Commit and push only when the user asks for it.
- Run the relevant unit tests before every commit.
- Stage files by name. Do not use `git add -A` or `git add .`.
- Do not commit the user's uncommitted work (for example `static/row/world.js`, `.gitignore`, `static/zoo*`, `tools/`, `static/shared/`) unless the user names it. If a file mixes the user's changes with ours, ask before committing it.
- Never commit private data: `recordings/` and `rower/measurements/` are personal and must stay out of git.
- Commit messages end with the `Co-Authored-By` line from the current session instructions.

## Android ANT+ logger (`android/`)
- Read `ANDROID_HANDOVER.md` first; the skill `.claude/skills/android-ant-logger/SKILL.md` has the build/install/debug workflow.
- **Hard constraint: the app only listens.** Never send anything to a sensor (no calibration, crank length, capabilities or "set" commands, no acknowledged/burst data). It is enforced by `Ant.requireReadOnly` in `UsbAnt.write` with tests; do not weaken it. Any exception needs the user's explicit approval first.
- The repo is public. Never create, write or read a file the user will put a secret into (keystore passwords, keys): assistant tooling shows file changes. Signing details live in `E:\jschlager\keys\signing.properties` (the user's), passed to Gradle through `ANTLOGGER_SIGNING_PROPS`.
- Scratch files, screenshots and pulled captures go in `.claude/tmp` (git-ignored), never in the system temp folder.
- The phone is the user's personal phone: take screenshots only when asked or while they test the app, delete them afterwards, check for a running recording (`dumpsys activity services app.antlogger`) before installing, and do not tap or launch things while they use it.
- Run the Android unit tests (`gradlew testDebugUnitTest` in `android/`) before committing; keep `ANDROID_HANDOVER.md` current when findings change.

## Rowing app
- The rower is never asked to type values while rowing. Calibration detection is automatic, and the rower is assumed to hold the demanded speed.
- `ROWER_HANDOVER.md` is the source of truth for the rowing work. Update it when findings change.
