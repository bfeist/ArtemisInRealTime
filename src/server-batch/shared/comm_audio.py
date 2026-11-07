"""Prepare private audio derivatives without changing retained publications."""

import os
import re
import shutil
from collections import Counter, defaultdict
from uuid import uuid4



def audio_relative_path(source):
    """Match the viewer's legacy date-folder rule without using utterance UTC."""
    if not isinstance(source, str) or not source or any(c in source for c in "\\:\x00"):
        raise ValueError(f"Invalid comm audio reference: {source!r}")
    if any(part in {"", ".", ".."} for part in source.split("/")):
        raise ValueError(f"Invalid comm audio reference: {source!r}")
    if "/" not in source:
        date = re.search(r"\d{4}-\d{2}-\d{2}", source)
        if date:
            source = date[0] + "/" + source
    return source


def _inside(root, relative):
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Comm audio path escapes {root}: {path}")
    return path


def _roots(mission):
    web = mission.web_dir / "comm"
    cache = mission.comm_audio_cache
    if not web.resolve().is_relative_to(mission.web_dir.resolve()):
        raise ValueError("web/comm resolves outside the mission web directory")
    if not cache.resolve().is_relative_to((mission.data_dir / "processed").resolve()):
        raise ValueError("Comm audio cache resolves outside the mission processed directory")
    if cache.resolve().is_relative_to(web.resolve()) or web.resolve().is_relative_to(cache.resolve()):
        raise ValueError("Comm audio cache and publication directories must be separate")
    return web, cache


def _files(root):
    files = {}
    if root.exists():
        for path in root.rglob("*"):
            relative = path.relative_to(root).as_posix()
            _inside(root, relative)
            if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                raise ValueError(f"Linked paths are not supported in web/comm: {path}")
            if path.is_file():
                files[relative] = path
    return files


def _publish(source, target):
    """Link same-drive immutable derivatives; fall back to an atomic copy."""
    if target.exists() and source.samefile(target):
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + "." + uuid4().hex + ".tmp")
    try:
        try:
            os.link(source, temporary)
        except OSError:
            shutil.copy2(source, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def cached_audio_path(mission, source):
    """Reuse legacy prepared audio when an importer first switches to the cache."""
    web, cache = _roots(mission)
    relative = audio_relative_path(source)
    cached = _inside(cache, relative)
    legacy = _inside(web, relative)
    if not cached.exists() and legacy.is_file() and legacy.stat().st_size:
        _publish(legacy, cached)
    return cached


def prepare_comm_audio(mission, rows):
    """Preflight every final reference before changing any published indexes.

    Existing web audio is adopted into the cache on the first run. Imports and
    transcription write new derivatives there, so future builds need no migration.
    Missing/empty playback files fail the build before any retirement occurs.
    """
    web, cache = _roots(mission)
    published = _files(web)
    counts = Counter()
    sources = defaultdict(set)
    for row in rows:
        relative = audio_relative_path(row[3])
        counts[relative] += 1
        sources[relative].add(row[3])
    if not counts:
        raise RuntimeError("Refusing to publish empty comm or retire all audio; check transcript inputs")

    selected = {}
    missing = []
    for relative in sorted(counts):
        cached = _inside(cache, relative)
        source = cached if cached.is_file() else published.get(relative)
        if source is None or not source.is_file() or source.stat().st_size == 0:
            missing.append(relative)
        else:
            selected[relative] = source
    if missing:
        raise RuntimeError(f"Missing or empty comm audio ({len(missing)}): {', '.join(missing[:10])}. "
                           "Run the corresponding transcription/PCD/loop import before step 1c.")

    manifest = {}
    for relative, source in selected.items():
        cached = _inside(cache, relative)
        _publish(source, cached)
        # Publication owns web assets; preparation only fills the private cache.
        manifest[relative] = {"bytes": cached.stat().st_size, "utterances": counts[relative],
                              "sources": sorted(sources[relative])}
    return manifest


def reconcile_comm_audio(mission, manifest):
    """Compatibility receipt only: published assets remain pinned for rollback.

    Final release publication owns reference closure. Physical retirement is a
    separate explicit operation that must account for all retained releases.
    """
    web, cache = _roots(mission)
    published = _files(web)
    if not manifest:
        raise RuntimeError("Refusing empty audio reconciliation")
    unreferenced = sorted(set(published) - set(manifest))
    return {"files": manifest, "retired_files": 0, "retired_bytes": 0,
            "retired": [], "unreferenced_legacy_files": unreferenced,
            "retirement_policy": "Retain assets referenced by active or rollback releases"}
