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
        self.photo = {'path': 'ảnh.png', 'stamp': worker.fingerprint(self.path), 'category': 'landscape'}
        self.events = []

    def test_scan_nested_and_corrupt_images(self):
        (self.root / 'nested').mkdir()
        Image.new('RGB', (4, 4)).save(self.root / 'nested' / 'b.jpg')
        (self.root / 'bad.png').write_text('not an image')
        worker.scan(self.root, self.events.append)
        photos = [e['photo'] for e in self.events if e['type'] == 'photo']
        self.assertEqual(len(photos), 2)
        self.assertTrue(all(p['thumbnail'].startswith('data:image/jpeg;base64,') for p in photos))
        self.assertEqual(sum(e['type'] == 'warning' for e in self.events), 1)

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
        self.assertEqual(self.events[-1]['type'], 'done')

    def test_changed_source_not_classified(self):
        self.photo['stamp'] = 'old'
        class Classifier:
            def predict(self, path):
                raise AssertionError('Must not run inference')
        worker.classify(self.root, [self.photo], .55, self.events.append, Classifier())
        self.assertTrue(any(e['type'] == 'photo_error' for e in self.events))
        self.assertFalse(any(e['type'] == 'classified' for e in self.events))

    def test_copy_preserves_original_and_handles_collision(self):
        destination = self.base / 'organized'
        worker.copy_photos(self.root, destination, [self.photo, self.photo], self.events.append)
        self.assertEqual(self.events[-1]['count'], 2)
        self.assertEqual(self.path.read_bytes(), (destination / 'landscape' / 'ảnh.png').read_bytes())
        self.assertEqual(self.path.read_bytes(), (destination / 'landscape' / 'ảnh (1).png').read_bytes())
        records = [json.loads(line) for line in Path(self.events[-1]['journal']).read_text().splitlines()]
        self.assertEqual(sum(r['type'] == 'copied' for r in records), 2)
        self.assertFalse(list(destination.rglob('*.partial')))

    def test_copy_rejects_overlap(self):
        for path in [self.root, self.root / 'nested', self.base]:
            with self.assertRaises(ValueError):
                worker.copy_photos(self.root, path, [self.photo], self.events.append)

    def test_copy_rejects_invalid_category_and_changed_file(self):
        for photo in [{**self.photo, 'category': '../escape'}, {**self.photo, 'stamp': 'changed'}]:
            events = []
            worker.copy_photos(self.root, self.base / 'output', [photo], events.append)
            self.assertEqual(events[-1]['count'], 0)
            self.assertTrue(any(e['type'] == 'photo_error' for e in events))

    def test_copy_rejects_symlink_destination_category(self):
        destination = self.base / 'output'
        destination.mkdir()
        (destination / 'landscape').symlink_to(self.root, target_is_directory=True)
        worker.copy_photos(self.root, destination, [self.photo], self.events.append)
        self.assertEqual(self.events[-1]['count'], 0)

    def test_failed_copy_cleans_temporary_file(self):
        destination = self.base / 'output'
        with patch.object(worker.shutil, 'copyfileobj', side_effect=OSError('disk full')):
            worker.copy_photos(self.root, destination, [self.photo], self.events.append)
        self.assertEqual(self.events[-1]['count'], 0)
        self.assertFalse(list(destination.rglob('*.partial')))
        self.assertFalse(list(destination.rglob('*.png')))
        self.assertTrue(self.path.exists())

    def test_json_protocol_in_subprocess(self):
        result = subprocess.run([sys.executable, str(Path(worker.__file__))], input=json.dumps({'operation':'scan','source':str(self.root)}), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        events = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([e['type'] for e in events], ['photo', 'done'])

    def test_protocol_errors_exit_nonzero(self):
        result = subprocess.run([sys.executable, str(Path(worker.__file__))], input='{}', text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)['type'], 'error')


if __name__ == '__main__':
    unittest.main()
