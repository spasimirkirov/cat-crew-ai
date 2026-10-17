# README-first repo orientation

Use when the user wants to *understand a repo* and the repo has a README that points at other authoritative documents (a doc graph). The README is the map — follow it before synthesizing.

## Procedure (in order)

1. **Read the README first.** Treat it as the map, not as the whole picture. Note every explicit document reference it gives (docs/, SKILL.md, backlog files, config examples).
2. **Batch-read the referenced documents next**, before any synthesis or "here's what the project is" summary. Do not stop at the README and infer the rest.
3. **Synthesize only after the doc graph is loaded.** The product shape, rules, and "what is already shipped" usually live across the referenced docs, not in the README alone.

## Pitfalls

- **Summarizing from the README alone when it explicitly points elsewhere.** The README here is a pointer to deeper authoritative docs; a partial read gives a partial and often misleading picture.
- **Reading one referenced doc and skipping the rest.** When a README lists several documents, each usually carries a distinct slice (product map, overlay notes, steal backlog, config). Skipping one drops part of the spec.
- **Asking "what do you want me to do?" when the README already says "read these docs to understand."** The instruction is already there; the work is to follow the graph, not to negotiate it.

## When this does not apply

- The README has no doc references and the repo is small enough to read directly.
- The user explicitly wants a narrow answer (e.g. "what does file X do") that does not require the full doc graph.
