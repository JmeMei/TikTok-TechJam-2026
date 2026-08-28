---
description: Answer a question about evaluator behaviour from source, never from assumption
---
Delegate to the `spec-oracle` subagent.

Question: $ARGUMENTS

If no question is given, answer all six standing questions in CLAUDE.md section 12, then
rewrite that section in place with the answers and their file:line evidence.

Every answer needs: the answer in one line, quoted evidence with file and line number, and
the consequence for our agent's design.
