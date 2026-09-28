"""Fail if the Git index includes local data, credentials or generated artifacts."""

from pathlib import Path, PurePosixPath
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_DIRS = {".archive", ".venv-release", "GF2TTK", "Example", "captures", "backups", "drafts", "build", "dist", "release", ".vscode", "__pycache__", ".pytest_cache"}
FORBIDDEN_NAMES = {"credentials.json", "settings.json", ".env", "CLAUDE.md"}


def main() -> None:
    paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode("utf-8").split("\0")
    errors = []
    for name in filter(None, paths):
        path = PurePosixPath(name)
        if (set(path.parts) & FORBIDDEN_DIRS or path.name in FORBIDDEN_NAMES or
            path.suffix.lower() in {".db", ".sqlite", ".sqlite3", ".exe", ".zip", ".log", ".pyc", ".code-workspace"} or
            name.startswith("docs/audit-") or name in {"scripts/syntax_check.py", "docs/improvements.md"}):
            errors.append(f"Local/generated file staged: {name}")
        blob = subprocess.check_output(["git", "show", f":{name}"], cwd=ROOT)
        if len(blob) > 10 * 1024 * 1024:
            errors.append(f"Oversized source file: {name}")
        if re.search(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", blob):
            errors.append(f"Private key found: {name}")
        if re.search(rb'"type"\s*:\s*"service_account"', blob):
            errors.append(f"Service account credential found: {name}")
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"Git source manifest: {len([p for p in paths if p])} files checked; no local data or private keys.")


if __name__ == "__main__":
    main()
