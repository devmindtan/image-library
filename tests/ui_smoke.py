"""Browser/IPC contract smoke test. Real scan/copy; classification events are fixtures.
Start Vite separately with npm run dev. This does not test the native Tauri window.
"""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from PIL import Image
from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[1]

with tempfile.TemporaryDirectory() as directory, sync_playwright() as p:
    source = Path(directory) / 'source'
    destination = Path(directory) / 'output'
    source.mkdir()
    destination.mkdir()
    Image.new('RGB', (80, 60), 'green').save(source / 'forest.png')
    Image.new('RGB', (60, 80), 'blue').save(source / 'portrait.png')
    originals = {path.name: path.read_bytes() for path in source.iterdir()}
    browser = p.chromium.launch(executable_path=shutil.which('chromium'), args=['--no-sandbox'])
    page = browser.new_page(viewport={'width': 1280, 'height': 900})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))

    def worker(request):
        if request['operation'] == 'classify':
            # Fixture only: inference with real CLIP weights needs separate validation.
            return [{'type': 'classified', 'path': photo['path'], 'category': 'landscape', 'score': .8}
                    for photo in request['photos']] + [{'type': 'done', 'count': len(request['photos'])}]
        result = subprocess.run([sys.executable, str(REPO / 'backend/worker.py')], input=json.dumps(request), text=True, capture_output=True)
        assert result.returncode == 0, result.stdout + result.stderr
        return [json.loads(line) for line in result.stdout.splitlines()]

    page.expose_function('testWorker', worker)
    page.add_init_script('''
        window.isTauri = true;
        window.__TAURI_INTERNALS__ = {
          transformCallback: () => 1, unregisterCallback: () => {},
          invoke: async (command, args) => {
            if (command === 'plugin:dialog|open') return args.options.title.includes('gốc') ? SOURCE : DESTINATION;
            if (command === 'run_worker') {
              for (const event of await window.testWorker(args.request)) args.onEvent.onmessage(event);
              return;
            }
            if (command === 'cancel_worker') return;
            throw Error('Unexpected command: ' + command);
          }
        };
    '''.replace('SOURCE', json.dumps(str(source))).replace('DESTINATION', json.dumps(str(destination))))
    page.goto('http://127.0.0.1:1420/', wait_until='networkidle')
    page.get_by_role('button', name='＋ Chọn thư mục ảnh').click()
    page.wait_for_function("document.querySelectorAll('.photo-card').length === 2")
    page.get_by_role('button', name='Phân loại ảnh').click()
    page.wait_for_function("document.querySelectorAll('.photo-info select').length === 2")
    page.locator('.photo-info select').first.select_option('review')
    page.get_by_role('button', name='Chọn nơi lưu').click()
    page.get_by_role('button', name='Sao chép theo nhóm').click()
    assert page.get_by_role('dialog').is_visible()
    assert not list(destination.rglob('*.png')), 'No copy before confirmation'
    page.get_by_role('button', name='Quay lại').click()
    assert not list(destination.rglob('*.png'))
    page.get_by_role('button', name='Sao chép theo nhóm').click()
    page.get_by_role('button', name='Xác nhận sao chép').click()
    page.wait_for_function("document.querySelectorAll('.copied-badge').length === 2")
    assert len(list(destination.rglob('*.png'))) == 2
    assert len(list((destination / 'review').glob('*.png'))) == 1
    assert len(list((destination / 'landscape').glob('*.png'))) == 1
    assert {path.name: path.read_bytes() for path in source.iterdir()} == originals
    assert len(list(destination.glob('image-library-*.jsonl'))) == 1
    assert not errors, errors
    browser.close()
    print('PASS: scan → fixture classification → manual review → confirmation/cancel dialog → real copy; originals unchanged.')
