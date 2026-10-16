import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import worker
from PIL import Image


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'source'
        self.root.mkdir()
        self.path = self.root / 'ảnh.png'
        Image.new('RGB', (24, 16), 'green').save(self.path)
        self.photo = self.entry(self.path, 'landscape')
        self.events = []
        self.destination = self.base / 'organized'

    def entry(self, path, category=None):
        return {'path': str(path.relative_to(self.root)), 'stamp': worker.fingerprint(path), 'category': category}

    def execute(self, photos=None, mode='copy', strategy='content', plan=None):
        photos = [self.photo] if photos is None else photos
        if plan is None:
            plan = worker.organization_plan(self.root, photos, mode, strategy, self.destination)
        worker.apply_organization(self.root, photos, mode, strategy, plan, self.destination, self.events.append)

    def test_scan_nested_and_corrupt_images(self):
        (self.root / 'nested').mkdir()
        Image.new('RGB', (4, 4)).save(self.root / 'nested' / 'b.jpg')
        (self.root / 'bad.png').write_text('not an image')
        worker.scan(self.root, self.events.append)
        photos = [e['photo'] for e in self.events if e['type'] == 'photo']
        self.assertEqual(len(photos), 3)
        self.assertEqual(sum(p['thumbnail'].startswith('data:image/jpeg;base64,') for p in photos), 2)
        self.assertTrue(next(p for p in photos if p['path'] == 'bad.png')['previewError'])
        self.assertEqual(sum(e['type'] == 'warning' for e in self.events), 1)
        self.assertEqual(self.events[-1]['bytes'], sum(p.stat().st_size for p in self.root.rglob('*') if p.is_file()))

    def test_scan_video_kind_size_and_no_thumbnail_decode(self):
        video = self.root / 'clip.MP4'
        video.write_bytes(b'video fixture' * 100)
        worker.scan(self.root, self.events.append)
        item = next(e['photo'] for e in self.events if e['type'] == 'photo' and e['photo']['kind'] == 'video')
        self.assertEqual(item['size'], video.stat().st_size)
        self.assertEqual(item['category'], 'videos')
        self.assertEqual(item['thumbnail'], '')
        self.assertEqual(self.events[-1]['images'], 1)
        self.assertEqual(self.events[-1]['videos'], 1)
        self.assertEqual(self.events[-1]['bytes'], video.stat().st_size + self.path.stat().st_size)

    def test_scan_infers_existing_folders_without_mutation(self):
        (self.root / 'landscape').mkdir()
        moved = self.root / 'landscape' / self.path.name
        self.path.rename(moved)
        before = set(self.root.rglob('*'))
        worker.scan(self.root, self.events.append)
        self.assertEqual(self.events[0]['photo']['category'], 'landscape')
        self.assertEqual(set(self.root.rglob('*')), before)

    def test_scan_skips_symlinks(self):
        (self.root / 'linked.png').symlink_to(self.path)
        (self.root / 'loop').symlink_to(self.root, target_is_directory=True)
        worker.scan(self.root, self.events.append)
        self.assertEqual(self.events[-1]['count'], 1)

    def test_path_traversal_and_absolute_paths_rejected(self):
        for value in ['../outside.png', str(self.path), 'nested/../../outside.png']:
            with self.assertRaises(ValueError):
                worker.source_path(self.root, value)

    def test_symlink_source_rejected(self):
        (self.root / 'linked.png').symlink_to(self.path)
        with self.assertRaises(ValueError):
            worker.source_path(self.root, 'linked.png')

    def test_threshold_routes_to_review(self):
        class Classifier:
            def predict(self, path):
                return 'landscape', .4
        worker.classify(self.root, [self.photo], .55, self.events.append, Classifier())
        event = next(e for e in self.events if e['type'] == 'classified')
        self.assertEqual(event['category'], 'review')

    def test_videos_do_not_load_or_call_model(self):
        video = self.root / 'clip.mov'
        video.write_bytes(b'fixture')
        with patch.object(worker, 'ClipClassifier', side_effect=AssertionError('Must not load AI')):
            worker.classify(self.root, [self.entry(video)], .55, self.events.append)
        event = next(e for e in self.events if e['type'] == 'classified')
        self.assertEqual(event['category'], 'videos')
        self.assertNotIn('score', event)

    def test_unavailable_model_aborts_once(self):
        with patch.object(worker, 'ClipClassifier', side_effect=OSError('offline')) as model:
            with self.assertRaisesRegex(RuntimeError, 'CLIP'):
                worker.classify(self.root, [self.photo, self.photo], .55, self.events.append)
        self.assertEqual(model.call_count, 1)

    def test_changed_source_not_classified(self):
        self.photo['stamp'] = 'old'
        class Classifier:
            def predict(self, path):
                raise AssertionError('Must not run inference')
        worker.classify(self.root, [self.photo], .55, self.events.append, Classifier())
        self.assertTrue(any(e['type'] == 'photo_error' for e in self.events))

    def test_copy_preserves_original_and_handles_collisions_within_plan(self):
        (self.root / 'nested').mkdir()
        duplicate = self.root / 'nested' / self.path.name
        duplicate.write_bytes(self.path.read_bytes())
        self.execute([self.photo, self.entry(duplicate, 'landscape')])
        self.assertEqual(self.events[-1]['count'], 2)
        self.assertEqual(self.path.read_bytes(), (self.destination / 'landscape' / 'ảnh.png').read_bytes())
        self.assertEqual(self.path.read_bytes(), (self.destination / 'landscape' / 'ảnh (1).png').read_bytes())
        records = [json.loads(line) for line in Path(self.events[-1]['journal']).read_text().splitlines()]
        self.assertEqual(sum(r['type'] == 'copied' for r in records), 2)
        self.assertFalse(list(self.destination.rglob('*.partial')))

    def test_copy_rejects_overlap(self):
        for path in [self.root, self.root / 'nested', self.base]:
            with self.assertRaises(ValueError):
                worker.organization_plan(self.root, [self.photo], 'copy', 'type', path)

    def test_plan_is_read_only_and_type_sort_needs_no_ai(self):
        before = set(self.root.rglob('*'))
        plan = worker.organization_plan(self.root, [{**self.photo, 'category': None}], 'copy', 'type', self.destination)
        self.assertEqual(plan[0]['target'], str(Path('images') / self.path.name))
        self.assertFalse(self.destination.exists())
        self.assertEqual(set(self.root.rglob('*')), before)

    def test_plan_rejects_invalid_categories_and_changed_files(self):
        for photo in [{**self.photo, 'category': '../escape'}, {**self.photo, 'stamp': 'changed'}, {**self.photo, 'category': 'videos'}]:
            with self.assertRaises(ValueError):
                worker.organization_plan(self.root, [photo], 'inplace', 'content')

    def test_plan_rejects_duplicate_source(self):
        with self.assertRaises(ValueError):
            worker.organization_plan(self.root, [self.photo, self.photo], 'inplace', 'type')

    def test_plan_rejects_symlink_destination_category(self):
        self.destination.mkdir()
        (self.destination / 'landscape').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            worker.organization_plan(self.root, [self.photo], 'copy', 'content', self.destination)

    def test_failed_copy_cleans_temporary_file(self):
        with patch.object(worker.shutil, 'copyfileobj', side_effect=OSError('disk full')):
            self.execute()
        self.assertEqual(self.events[-1]['count'], 0)
        self.assertFalse(list(self.destination.rglob('*.partial')))
        self.assertFalse(list(self.destination.rglob('*.png')))
        self.assertTrue(self.path.exists())

    def test_inplace_move_image_video_and_rescan_is_idempotent(self):
        original = self.path.read_bytes()
        video = self.root / 'clip.mp4'
        video.write_bytes(b'video fixture')
        self.execute([self.photo, self.entry(video)], mode='inplace', strategy='type')
        self.assertFalse(self.path.exists())
        self.assertFalse(video.exists())
        self.assertEqual((self.root / 'images' / self.path.name).read_bytes(), original)
        self.assertEqual((self.root / 'videos' / video.name).read_bytes(), b'video fixture')
        events = []
        worker.scan(self.root, events.append)
        entries = [e['photo'] for e in events if e['type'] == 'photo']
        plan = worker.organization_plan(self.root, entries, 'inplace', 'type')
        self.assertTrue(all(item['action'] == 'skip' for item in plan))
        self.assertEqual(events[-1]['count'], 2)
        self.assertEqual({p['category'] for p in entries}, {'images', 'videos'})

    def test_existing_destination_never_overwritten(self):
        (self.root / 'images').mkdir()
        target = self.root / 'images' / self.path.name
        target.write_bytes(b'existing')
        self.execute(mode='inplace', strategy='type')
        self.assertEqual(target.read_bytes(), b'existing')
        self.assertTrue((target.parent / 'ảnh (1).png').exists())

    def test_stale_plan_rejected_before_mutation(self):
        plan = worker.organization_plan(self.root, [self.photo], 'inplace', 'type')
        (self.root / 'images').mkdir()
        (self.root / 'images' / self.path.name).write_bytes(b'appeared later')
        with self.assertRaisesRegex(ValueError, 'xem trước'):
            self.execute(mode='inplace', strategy='type', plan=plan)
        self.assertTrue(self.path.exists())
        self.assertFalse(list(self.root.glob('*.jsonl')))

    def test_edited_plan_rejected(self):
        plan = worker.organization_plan(self.root, [self.photo], 'inplace', 'type')
        plan[0]['target'] = '../escape.png'
        with self.assertRaises(ValueError):
            self.execute(mode='inplace', strategy='type', plan=plan)
        self.assertTrue(self.path.exists())

    def test_link_failure_keeps_source(self):
        with patch.object(worker.os, 'link', side_effect=OSError('filesystem lacks hard links')):
            self.execute(mode='inplace', strategy='type')
        self.assertTrue(self.path.exists())
        self.assertEqual(self.events[-1]['count'], 0)

    def test_unlink_failure_keeps_both_copies_for_recovery(self):
        with patch.object(Path, 'unlink', side_effect=OSError('permission denied')):
            self.execute(mode='inplace', strategy='type')
        self.assertTrue(self.path.exists())
        self.assertEqual((self.root / 'images' / self.path.name).read_bytes(), self.path.read_bytes())
        self.assertEqual(self.events[-1]['count'], 0)
        self.assertTrue(any(e['type'] == 'photo_error' for e in self.events))

    def test_json_protocol_in_subprocess(self):
        result = subprocess.run([sys.executable, str(Path(worker.__file__))], input=json.dumps({'operation':'scan','source':str(self.root)}), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        events = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([e['type'] for e in events], ['photo', 'done'])

    def test_image_metadata_exif_and_no_mutation(self):
        path = self.root / 'camera.jpg'
        exif = Image.Exif()
        exif[271] = 'Test Camera'
        exif[272] = 'Model One'
        Image.new('RGB', (80, 60)).save(path, exif=exif)
        before = path.read_bytes()
        detail = worker.metadata(self.root, self.entry(path))
        self.assertEqual(detail['image']['Chiều rộng'], 80)
        self.assertEqual(detail['image']['Chiều cao'], 60)
        self.assertEqual(detail['exif']['Make'], 'Test Camera')
        self.assertEqual(detail['general']['Dung lượng (bytes)'], len(before))
        self.assertEqual(path.read_bytes(), before)

    def test_metadata_rejects_changed_file(self):
        with self.assertRaises(ValueError):
            worker.metadata(self.root, {**self.photo, 'stamp': 'changed'})

    def test_video_metadata_reports_missing_ffprobe(self):
        path = self.root / 'clip.mp4'
        path.write_bytes(b'fixture')
        with patch.object(worker.subprocess, 'run', side_effect=FileNotFoundError('ffprobe')):
            detail = worker.metadata(self.root, self.entry(path))
        self.assertTrue(detail['warnings'])
        self.assertEqual(detail['general']['Loại'], 'video')

    def test_trash_prevalidates_entire_selection(self):
        calls = []
        with self.assertRaises(ValueError):
            worker.trash_files(self.root, [self.photo, {**self.photo, 'path': '../escape.png'}], self.events.append, calls.append)
        self.assertFalse(calls)
        self.assertTrue(self.path.exists())

    def test_trash_failure_never_falls_back_to_unlink(self):
        def unavailable(path):
            raise OSError('Trash unavailable')
        worker.trash_files(self.root, [self.photo], self.events.append, unavailable)
        self.assertTrue(self.path.exists())
        self.assertEqual(self.events[-1]['count'], 0)
        self.assertTrue(any(e['type'] == 'photo_error' for e in self.events))

    def test_trash_moves_to_recoverable_location(self):
        recovery = self.base / 'recovery'
        recovery.mkdir()
        original = self.path.read_bytes()
        def trash(path):
            Path(path).rename(recovery / Path(path).name)
        worker.trash_files(self.root, [self.photo], self.events.append, trash)
        self.assertFalse(self.path.exists())
        self.assertEqual((recovery / self.path.name).read_bytes(), original)
        self.assertEqual(self.events[-1]['count'], 1)
        self.assertTrue(any(e['type'] == 'trashed' for e in self.events))

    def test_protocol_errors_exit_nonzero(self):
        result = subprocess.run([sys.executable, str(Path(worker.__file__))], input='{}', text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)['type'], 'error')


if __name__ == '__main__':
    unittest.main()
