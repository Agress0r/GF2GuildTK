"""
Claude Code PostToolUse hook: checks Python syntax after Edit/Write operations.
Reads tool context from stdin (JSON), runs ast.parse() on modified .py files.
Exit code 2 = block the action (syntax error found).
"""
import ast
import json
import sys


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        # No stdin or invalid JSON — skip silently
        sys.exit(0)

    tool_name = data.get("tool_name", "")
    if tool_name not in ("Edit", "Write"):
        sys.exit(0)

    tool_input = data.get("tool_input", {})
    file_path = tool_input.get("file_path", "")

    if not file_path.endswith(".py"):
        sys.exit(0)

    try:
        with open(file_path, encoding="utf-8") as f:
            source = f.read()
        ast.parse(source, filename=file_path)
        print(f"Syntax OK: {file_path}")
        sys.exit(0)
    except SyntaxError as e:
        print(f"SYNTAX ERROR in {file_path}:{e.lineno}: {e.msg}", file=sys.stderr)
        print(f"  {e.text}", file=sys.stderr)
        sys.exit(2)
    except FileNotFoundError:
        # File was deleted — not an error
        sys.exit(0)
    except Exception as e:
        print(f"Warning: syntax check failed: {e}", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
