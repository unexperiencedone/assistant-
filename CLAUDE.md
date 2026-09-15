@AGENTS.md

## Claude Code specifics

- Use the dedicated Read, Grep and Glob tools for files inside a project. Use `sysindex.py` or the shell recipes above only for locating things elsewhere on the machine.
- The Bash tool runs Git Bash and the PowerShell tool runs Windows PowerShell 5.1. Pick one per command and use that shell's path style.
- When the voice assistant drives you, you're a live `claude -p` session and each spoken request is a new turn. The voice rules come in through `--append-system-prompt`: act directly, keep final replies to one to three spoken sentences, and use `[[PLAN: title]] ... [[/PLAN]]` blocks and `[[STEP n DONE]]` markers.
- Nobody can approve permission prompts mid-turn. If an action is denied or needs a real decision, say so in one sentence and the user will answer by voice on the next turn.
