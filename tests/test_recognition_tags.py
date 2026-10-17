"""Real audio verifies tag edits preserve audio packets and are reversible."""

from __future__ import annotations

import shutil
import subprocess

import pytest
from mutagen import File as MutagenFile
from mutagen import MutagenError

from downtify import recognition_tags

pytestmark = pytest.mark.skipif(
    shutil.which('ffmpeg') is None, reason='no ffmpeg'
)


def _audio_packets(path):
    return subprocess.run(
        [
            'ffmpeg',
            '-v',
            'error',
            '-i',
            str(path),
            '-map',
            '0:a',
            '-c:a',
            'copy',
            '-f',
            'hash',
            '-hash',
            'sha256',
            '-',
        ],
        capture_output=True,
        check=True,
    ).stdout


@pytest.mark.parametrize(
    ('fmt', 'codec'),
    [
        ('mp3', 'libmp3lame'),
        ('flac', 'flac'),
        ('m4a', 'aac'),
        ('ogg', 'libvorbis'),
        ('opus', 'libopus'),
    ],
)
def test_real_audio_survives_tag_edit_and_exact_restore(tmp_path, fmt, codec):
    path = tmp_path / f'amv.{fmt}'
    subprocess.run(
        [
            'ffmpeg',
            '-v',
            'error',
            '-f',
            'lavfi',
            '-i',
            'sine=frequency=440:duration=0.3',
            '-c:a',
            codec,
            '-metadata',
            'title=Slime AMV',
            '-metadata',
            'artist=Editor',
            str(path),
        ],
        check=True,
    )
    original, packets, mtime = (
        path.read_bytes(),
        _audio_packets(path),
        path.stat().st_mtime_ns,
    )
    backup = tmp_path / 'backups' / 'job'
    expected = recognition_tags.apply_tags(
        path, backup, 'Broken', 'Actual Musician'
    )
    assert backup.read_bytes() == original
    tags = MutagenFile(path, easy=True)
    assert tags['title'] == ['Broken']
    assert tags['artist'] == ['Actual Musician']
    assert _audio_packets(path) == packets
    assert path.stat().st_mtime_ns == mtime
    recognition_tags.restore_tags(path, backup, expected)
    assert path.read_bytes() == original


def test_undo_does_not_clobber_later_edits(tmp_path):
    path, backup = tmp_path / 'amv.mp3', tmp_path / 'backup'
    path.write_bytes(b'newer audio')
    backup.write_bytes(b'old audio')
    with pytest.raises(ValueError, match='changed after correction'):
        recognition_tags.restore_tags(path, backup, 'not-the-current-digest')
    assert path.read_bytes() == b'newer audio'


def test_failed_tag_edit_preserves_original_and_cleans_backup(tmp_path):
    path, backup = tmp_path / 'broken.mp3', tmp_path / 'backup'
    path.write_bytes(b'not audio')
    with pytest.raises(MutagenError):
        recognition_tags.apply_tags(path, backup, 'Song', 'Artist')
    assert path.read_bytes() == b'not audio'
    assert not backup.exists()
