"""Role-specific Letta-style executable tools (Phase 15, Task #22).

Each module here holds real Python functions for one role - Pulse
(developer), Forge (devops), Aegis (qa), Blaze (marketing), Lex (legal) -
registered into the ``tools`` /
``agent_tools`` catalog (``platform/tool_registry.py`` + ``platform/db.py``)
and assigned only to that role's bot, as a working demonstration of the
per-agent tool matrix: unlike the legacy global ``TOOLS_SCHEMA``
(``backend/tools.py``, available to every bot), a tool registered here is
only callable by the bot(s) it is assigned to.

Where a capability already exists globally (editing a file, running a
shell command, reading a git diff) these wrap the existing sandboxed
implementation in ``backend/tools.py`` rather than re-implementing path
safety or command filtering - the new value is the per-agent scoping, not
a second copy of that logic. Where the capability is genuinely new
(AST inspection, container status, endpoint auditing, screenshots) it is
implemented directly here.

See ``scripts/seed_role_tools.py`` for the one-time registration +
assignment step, and the CatCrewAI project doc
``architecture/phase15-dual-skill-tool-architecture.md`` for the design.
"""
