"""Report the compressed size of the main one-file bundle components."""

from collections import defaultdict
from pathlib import Path
import sys

from PyInstaller.archive.readers import CArchiveReader


def category(name: str) -> str:
    lower = name.lower().replace("\\", "/")
    for token, label in (
        ("ddddocr/", "ddddocr model"),
        ("rapidocr_onnxruntime/models/", "RapidOCR models"),
        ("pyqt6/", "Qt"),
        ("cv2/", "OpenCV"),
        ("onnxruntime/", "ONNX Runtime"),
        ("shapely", "Shapely"),
        ("scapy/", "Scapy"),
    ):
        if token in lower:
            return label
    return "Other Python, DLLs and assets"


def report(exe: Path) -> str:
    archive = CArchiveReader(str(exe))
    groups = defaultdict(int)
    files = []
    for name, entry in archive.toc.items():
        packed = entry[1]
        groups[category(name)] += packed
        files.append((packed, name))
    mib = 1024 * 1024
    lines = [
        "# GuildTracker one-file bundle size",
        "",
        f"EXE: {exe.stat().st_size / mib:.1f} MiB",
        "",
        "| Component | Packed MiB |",
        "|---|---:|",
    ]
    lines.extend(f"| {name} | {size / mib:.1f} |"
                 for name, size in sorted(groups.items(), key=lambda item: -item[1]))
    lines += ["", "## Largest entries", "", "| Entry | Packed MiB |", "|---|---:|"]
    lines.extend(f"| `{name}` | {size / mib:.1f} |"
                 for size, name in sorted(files, reverse=True)[:15])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    exe_path = Path(sys.argv[1]).resolve()
    output_path = Path(sys.argv[2]).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report(exe_path), encoding="utf-8")
    print(f"Bundle report: {output_path}")
