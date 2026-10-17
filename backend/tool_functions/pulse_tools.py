"""Pulse (developer) role-specific tools."""

from __future__ import annotations

import ast


def write_file_patch(path: str, old_string: str, new_string: str) -> str:
    """Replace one exact snippet in a source file with another.

    Thin wrapper over the sandboxed, path-checked ``replace_in_file`` in
    ``backend/tools.py`` - reuses its path validation and single-occurrence
    safety check rather than re-implementing them. Registered separately
    here so it can be assigned/unassigned per bot through the Tools
    matrix, independent of the always-on global tool of the same shape.

    Args:
        path: File to edit, relative to the workspace or CatCrewAI source.
        old_string: Exact snippet to find. Must occur exactly once.
        new_string: Text to replace it with.

    Returns:
        A short status string, or an ``Error:`` message.
    """
    from .. import tools as _tools

    return _tools.replace_in_file(path, old_string, new_string)


def git_diff_inspector(path: str = "", staged: bool = False) -> str:
    """Summarize a git diff: files touched and lines added/removed per file,
    followed by the raw diff.

    Unlike the plain ``git_diff`` tool (raw patch text only), this parses
    the diff into a per-file change summary first, so a bot can see the
    shape of a change (3 files, +42/-11) before reading the full patch.

    Args:
        path: Optional path to restrict the diff to. Empty means the whole repo.
        staged: If true, diff the staging area instead of the working tree.

    Returns:
        A summary of files changed plus the raw diff text.
    """
    from .. import tools as _tools

    raw = _tools.git_diff(path or None, staged=bool(staged))
    if not raw or raw.startswith("Error") or raw.strip() == "":
        return raw or "(no changes)"
    files: dict[str, dict[str, int]] = {}
    current = None
    for line in raw.splitlines():
        if line.startswith("diff --git"):
            parts = line.split(" b/")
            current = parts[-1].strip() if len(parts) > 1 else line[11:].strip()
            files[current] = {"added": 0, "removed": 0}
        elif current and line.startswith("+") and not line.startswith("+++"):
            files[current]["added"] += 1
        elif current and line.startswith("-") and not line.startswith("---"):
            files[current]["removed"] += 1
    if not files:
        return f"(no file headers found in diff)\n\n{raw}"
    summary_lines = [f"{len(files)} file(s) changed:"]
    for name, counts in files.items():
        summary_lines.append(f"  {name}: +{counts['added']} -{counts['removed']}")
    return "\n".join(summary_lines) + "\n\n--- raw diff ---\n" + raw


def read_code_ast(path: str) -> str:
    """Parse a Python file and summarize its structure: imports, top-level
    classes (with their methods) and top-level functions, with line numbers.

    Useful before editing an unfamiliar file - a structural map is faster
    to scan than the raw source, and catches a SyntaxError up front.

    Args:
        path: Python file to parse, relative to the workspace or CatCrewAI source.

    Returns:
        A structured text summary, or an ``Error:`` message.
    """
    from .. import tools as _tools

    try:
        file_path = _tools.resolve_user_path(path)
    except ValueError as exc:
        return f"Error: {exc}"
    if not file_path.is_file():
        return f"Error: file does not exist: {file_path}."
    if file_path.suffix != ".py":
        return f"Error: read_code_ast only supports Python files, got {file_path.suffix or '(no extension)'}."
    try:
        source = file_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError as exc:
        return f"Error: syntax error parsing {file_path.name}: {exc}"

    imports: list[str] = []
    classes: list[str] = []
    functions: list[str] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            imports.extend(f"{mod}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.ClassDef):
            methods = [
                n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            bases = [ast.unparse(b) for b in node.bases] if hasattr(ast, "unparse") else []
            base_note = f"({', '.join(bases)})" if bases else ""
            classes.append(
                f"  L{node.lineno}: class {node.name}{base_note} - methods: {', '.join(methods) or '(none)'}"
            )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = [a.arg for a in node.args.args]
            functions.append(f"  L{node.lineno}: def {node.name}({', '.join(args)})")

    lines = [f"{file_path.name} - {len(source.splitlines())} lines"]
    lines.append(f"\n{len(imports)} import(s):")
    lines.extend(f"  {imp}" for imp in imports[:40])
    lines.append(f"\n{len(classes)} top-level class(es):")
    lines.extend(classes)
    lines.append(f"\n{len(functions)} top-level function(s):")
    lines.extend(functions)
    return "\n".join(lines)
