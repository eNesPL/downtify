"""Explicit, reversible title/artist tag edits; audio is never transcoded."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path

from mutagen import File as MutagenFile

from .library_catalog import UPGRADE_STAGING_MARKER


def digest(path: Path) -> str:
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def apply_tags(path: Path, backup: Path, title: str, artist: str) -> str:
    """Back up the complete original before atomically changing tags."""
    if path.suffix.lower() not in {'.mp3', '.flac', '.m4a', '.ogg', '.opus'}:
        raise ValueError('This audio format does not support tag correction')
    if backup.exists():
        raise ValueError('Undo the previous tag correction first')
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup)
    try:
        with tempfile.TemporaryDirectory(
            prefix='recognition-', dir=path.parent
        ) as temp:
            staged = Path(temp) / f'track{UPGRADE_STAGING_MARKER}{path.suffix}'
            shutil.copy2(path, staged)
            audio = MutagenFile(staged, easy=True)
            if audio is None:
                raise ValueError('Could not read audio tags')
            if audio.tags is None:
                audio.add_tags()
            audio['title'] = [title]
            audio['artist'] = [artist]
            audio['albumartist'] = [artist]
            audio.save()
            if MutagenFile(staged) is None:
                raise ValueError('Could not verify the corrected audio file')
            stat = path.stat()
            os.utime(staged, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            result = digest(staged)
            os.replace(staged, path)
            return result
    except Exception:
        backup.unlink(missing_ok=True)
        raise


def restore_tags(path: Path, backup: Path, expected_digest: str) -> None:
    """Refuse to overwrite any later library edit or audio replacement."""
    if not backup.is_file():
        raise ValueError('The original tag backup is missing')
    if digest(path) != expected_digest:
        raise ValueError(
            'The file changed after correction; it cannot be safely restored'
        )
    with tempfile.TemporaryDirectory(
        prefix='recognition-', dir=path.parent
    ) as temp:
        staged = Path(temp) / f'track{UPGRADE_STAGING_MARKER}{path.suffix}'
        shutil.copy2(backup, staged)
        os.replace(staged, path)
