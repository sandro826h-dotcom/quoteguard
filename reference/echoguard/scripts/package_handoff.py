"""Build a reproducible, credential-scanned project handoff archive."""
from __future__ import annotations

import hashlib
import argparse
import io
import json
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INCLUDED_DIRS = ("configs", "data", "docs", "output", "prompts", "references", "reports", "runs", "scripts", "source_archives", "src", "tests")
SECRET = re.compile(rb"(?<![A-Za-z0-9])sk-[A-Za-z0-9_.-]{16,}")


def inspect_bytes(content: bytes, name: str, depth: int = 0) -> None:
    if SECRET.search(content):
        raise ValueError(f"Possible credential detected in {name}; archive not created")
    if name.lower().endswith(".zip"):
        if depth >= 6:
            raise ValueError(f"Nested archive exceeds inspection depth: {name}")
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            for info in archive.infolist():
                if not info.is_dir():
                    inspect_bytes(archive.read(info), f"{name}!{info.filename}", depth + 1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name', default='EchoGuard_Gate1_20261003_live.zip')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]+\.zip', args.name):
        parser.error('name must be an ASCII zip basename')
    destination = ROOT / "deliverables" / args.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    paths = [p for p in ROOT.iterdir() if p.is_file() and p.suffix in (".md", ".toml", ".txt")]
    for folder in INCLUDED_DIRS:
        paths.extend(p for p in (ROOT / folder).rglob("*") if p.is_file()
                     and "__pycache__" not in p.parts and ".pytest_cache" not in p.parts
                     and not any(part.endswith(".egg-info") for part in p.parts) and p.suffix != ".pyc")
    entries = {}
    for path in sorted(set(paths)):
        relative = path.relative_to(ROOT).as_posix()
        content = path.read_bytes()
        inspect_bytes(content, relative)
        entries[relative] = {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
    archive_manifest = {"created_for": "EchoGuard Gate 1 execution 2026-10-02 to 2026-10-03", "files": entries,
                        "excluded": [".venv", ".idea", "handoff_downloads", "deliverables", "caches"],
                        "credential_scan": "Passed sk-style API-key pattern scan of files and nested archives; this scan is not proof against all secret formats. Secret input was kept only in process memory."}
    manifest_bytes = (json.dumps(archive_manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in sorted(entries):
            archive.write(ROOT / relative, "EchoGuard/" + relative)
        archive.writestr("EchoGuard/PACKAGE_MANIFEST_20261003.json", manifest_bytes)
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise ValueError("Archive integrity check failed")
        for relative, meta in entries.items():
            if hashlib.sha256(archive.read("EchoGuard/" + relative)).hexdigest() != meta["sha256"]:
                raise ValueError(f"Archive readback mismatch: {relative}")
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    (destination.with_suffix(".sha256.txt")).write_text(digest + "  " + destination.name + "\n", encoding="utf-8")
    print(json.dumps({"archive": str(destination), "files": len(entries), "bytes": destination.stat().st_size,
                      "sha256": digest, "credential_scan": "passed", "readback": "passed"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
