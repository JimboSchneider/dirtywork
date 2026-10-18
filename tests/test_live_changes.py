"""Harness-published previews never affect the model run or saved export."""
import json
from datetime import datetime, timezone

from dirtywork.live_changes import LiveChangesPublisher

NOW = datetime(2026, 10, 18, 20, tzinfo=timezone.utc)


def test_publishes_each_successful_capture_and_leaves_export_untouched(tmp_path):
    saved = tmp_path / 'diff.patch'
    saved.write_text('exported result')
    patches = iter([b'first patch', b'second patch'])
    publisher = LiveChangesPublisher(tmp_path, capture=lambda: next(patches), now=lambda: NOW)
    publisher()
    first = json.loads((tmp_path / 'live_changes.json').read_text())
    assert first == {'version': 1, 'state': 'ready', 'patch': 'first patch',
                     'captured_at': NOW.isoformat(), 'message': 'Live file changes · updates after worker actions'}
    publisher()
    assert json.loads((tmp_path / 'live_changes.json').read_text())['patch'] == 'second patch'
    assert saved.read_text() == 'exported result'
    assert not list(tmp_path.glob('*.tmp'))


def test_capture_failure_is_visible_and_does_not_raise(tmp_path):
    def failed():
        raise RuntimeError('private command contents')
    LiveChangesPublisher(tmp_path, capture=failed, now=lambda: NOW)()
    record = json.loads((tmp_path / 'live_changes.json').read_text())
    assert record['state'] == 'unavailable'
    assert record['patch'] == ''
    assert 'private command' not in record['message']


def test_oversized_preview_is_not_written_as_a_partial_diff(tmp_path):
    LiveChangesPublisher(tmp_path, capture=lambda: b'x' * (1024 * 1024 + 1))()
    record = json.loads((tmp_path / 'live_changes.json').read_text())
    assert record['state'] == 'too_large'
    assert record['patch'] == ''


def test_unwritable_run_directory_does_not_fail_worker(tmp_path):
    publisher = LiveChangesPublisher(tmp_path / 'missing', capture=lambda: b'patch')
    publisher()


def test_preview_replaces_symlink_instead_of_writing_through_it(tmp_path):
    outside = tmp_path / 'outside'
    outside.write_text('keep')
    (tmp_path / 'live_changes.json').symlink_to(outside)
    LiveChangesPublisher(tmp_path, capture=lambda: b'patch')()
    assert outside.read_text() == 'keep'
    assert not (tmp_path / 'live_changes.json').is_symlink()


def test_json_expansion_is_also_bounded(tmp_path):
    LiveChangesPublisher(tmp_path, capture=lambda: b'\x01' * (1024 * 1024))()
    record = json.loads((tmp_path / 'live_changes.json').read_text())
    assert record['state'] == 'too_large'
    assert record['patch'] == ''


def test_non_utf8_replacement_expansion_is_reported_as_too_large(tmp_path):
    LiveChangesPublisher(tmp_path, capture=lambda: b'\xe9' * 400000)()
    record = json.loads((tmp_path / 'live_changes.json').read_text())
    assert record['state'] == 'too_large'
    assert record['patch'] == ''
