"""Package only explicitly allowed release files, then verify the standalone EXE."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app_version import VERSION


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, encoding="utf-8").strip()


def release_notes(changelog: str, version: str) -> str:
    heading = f"## [{version}]"
    lines = changelog.splitlines()
    start = next((i for i, line in enumerate(lines) if line == heading), None)
    if start is None:
        raise ValueError(f"CHANGELOG.md has no section {heading}")
    stop = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## [")), len(lines))
    return "\n".join(lines[start:stop]).strip() + "\n"


def main() -> None:
    exe = ROOT / "dist" / "GuildTracker.exe"
    if not exe.is_file():
        raise FileNotFoundError("Build dist/GuildTracker.exe first")
    # Run a copy with no adjacent resources, user settings or database.
    with tempfile.TemporaryDirectory(prefix="guildtracker-release-") as temporary:
        temporary = Path(temporary)
        standalone = temporary / exe.name
        shutil.copy2(exe, standalone)
        result_path = temporary / "check.json"
        process = subprocess.run([str(standalone), "--release-check", str(result_path)],
                                 cwd=temporary, capture_output=True, text=True, timeout=90)
        if not result_path.exists():
            raise RuntimeError(f"Packaged check exited {process.returncode}: {process.stderr}")
        check = json.loads(result_path.read_text(encoding="utf-8"))
        if process.returncode or check.get("version") != VERSION or not check.get("ok"):
            raise ValueError(f"Packaged application check failed: {check}")
        if (temporary / "settings.json").exists() or (temporary / "guild_tracker.db").exists():
            raise ValueError("Packaged smoke test unexpectedly created user data")

    notes = release_notes((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), VERSION)
    folder = ROOT / "release" / VERSION
    folder.mkdir(parents=True, exist_ok=True)
    zip_name = f"GuildTracker-{VERSION}-windows-x64.zip"
    expected = {"GuildTracker.exe", zip_name, "SHA256SUMS.txt", "release-notes.md", "build-info.json"}
    unexpected = {p.name for p in folder.iterdir()} - expected
    if unexpected:
        raise ValueError(f"Unexpected release files; move them aside before packaging: {sorted(unexpected)}")
    shutil.copy2(exe, folder / exe.name)
    (folder / "release-notes.md").write_text(notes, encoding="utf-8")
    with zipfile.ZipFile(folder / zip_name, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(exe, "GuildTracker.exe")
        archive.writestr("README.txt", (ROOT / "packaging" / "README.txt").read_text(encoding="utf-8").replace("{version}", VERSION))
        archive.writestr("CHANGELOG.md", notes)
    with zipfile.ZipFile(folder / zip_name) as archive:
        if set(archive.namelist()) != {"GuildTracker.exe", "README.txt", "CHANGELOG.md"} or archive.testzip():
            raise ValueError("Release ZIP verification failed")

    manifest = {
        "version": VERSION, "platform": "windows-x64", "python": platform.python_version(),
        "source_commit": git("rev-parse", "HEAD"),
        "source_dirty": bool(git("status", "--porcelain")),
        "requirements_sha256": sha256(ROOT / "requirements-release.txt"),
        "packaged_check": check,
    }
    (folder / "build-info.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    hashes = [f"{sha256(folder / name)}  {name}" for name in sorted(expected - {"SHA256SUMS.txt"})]
    (folder / "SHA256SUMS.txt").write_text("\n".join(hashes) + "\n", encoding="ascii")
    print(f"Release {VERSION} ready: {folder}")
    print("Packaged offline smoke test and ZIP contents: OK. Nothing published.")


if __name__ == "__main__":
    main()
