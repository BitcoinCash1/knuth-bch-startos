#!/usr/bin/env python3
"""Update the package version and image pin before the release commit is made."""

from pathlib import Path
import re
import sys


TAG = re.compile(r"v?([0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?)")
VERSION = re.compile(r"(?m)^([ \t]*version:[ \t]*)'([^'\r\n]+)'(?=[ \t]*,)")
NOTES = re.compile(r"(?m)^([ \t]*)releaseNotes:[ \t]*")
DOWNGRADE = re.compile(r"(?m)^const ALLOW_DOWNGRADE = (?:true|false)\b")
IMAGE = re.compile(r"(?m)^ARG KNUTH_VERSION=([^\r\n]+)")


def one_match(pattern: re.Pattern, source: str, name: str) -> re.Match:
    matches = list(pattern.finditer(source))
    if len(matches) != 1:
        raise ValueError(f"Expected one {name}, found {len(matches)}")
    return matches[0]


def value_end(source: str, start: int) -> int:
    """Find a property's comma without splitting quoted or nested values."""
    stack = []
    pairs = {"(": ")", "[": "]", "{": "}"}
    index = start
    while index < len(source):
        char = source[index]
        if char in "'\"`":
            quote = char
            index += 1
            while index < len(source):
                if source[index] == "\\":
                    index += 2
                elif source[index] == quote:
                    index += 1
                    break
                else:
                    index += 1
            else:
                raise ValueError("Unterminated releaseNotes string")
            continue
        if source.startswith("//", index):
            end = source.find("\n", index + 2)
            index = len(source) if end == -1 else end + 1
            continue
        if source.startswith("/*", index):
            end = source.find("*/", index + 2)
            if end == -1:
                raise ValueError("Unterminated releaseNotes comment")
            index = end + 2
            continue
        if char in pairs:
            stack.append(pairs[char])
        elif char in ")]}":
            if not stack or stack.pop() != char:
                raise ValueError("Unbalanced releaseNotes value")
        elif char == "," and not stack:
            return index
        index += 1
    raise ValueError("releaseNotes has no terminating comma")


def update_version(root: Path, tag: str) -> str | None:
    match = TAG.fullmatch(tag)
    if match is None:
        raise ValueError("Expected a Knuth version such as v1.4.0 or 1.4.0-rc.1")
    upstream = match[1]
    current_path = root / "startos/versions/current.ts"
    image_path = root / "Dockerfile"
    current = current_path.read_bytes().decode("utf-8")
    image = image_path.read_bytes().decode("utf-8")
    version = one_match(VERSION, current, "package version")
    image_version = one_match(IMAGE, image, "Dockerfile version pin")
    old_upstream, separator, revision = version[2].rpartition(":")
    if not separator or not revision.isdecimal() or TAG.fullmatch(old_upstream) is None:
        raise ValueError("Expected package version <upstream>:<revision>")
    if image_version[1] != old_upstream:
        raise ValueError("Package version and Dockerfile version pin disagree")
    if old_upstream == upstream:
        return None

    notes = one_match(NOTES, current, "releaseNotes property")
    notes_end = value_end(current, notes.end())
    downgrade = one_match(DOWNGRADE, current, "ALLOW_DOWNGRADE declaration")
    new_version = f"{upstream}:0"
    edits = [
        (version.start(2), version.end(2), new_version),
        (notes.start(), notes_end + 1,
         f"{notes[1]}releaseNotes: 'Upstream {tag}.',"),
        (downgrade.start(), downgrade.end(), "const ALLOW_DOWNGRADE = false"),
    ]
    for start, end, value in sorted(edits, reverse=True):
        current = current[:start] + value + current[end:]
    image = image[:image_version.start(1)] + upstream + image[image_version.end(1):]

    # Validate both files before either is written. A changed layout must fail
    # before the workflow can commit a package/image version mismatch.
    current_path.write_bytes(current.encode("utf-8"))
    image_path.write_bytes(image.encode("utf-8"))
    return new_version


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <tag>", file=sys.stderr)
        return 1
    try:
        version = update_version(Path.cwd(), sys.argv[1])
    except (OSError, ValueError) as error:
        print(f"Version update failed: {error}", file=sys.stderr)
        return 1
    if version is not None:
        print(version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
