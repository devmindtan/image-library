"""UI/IPC contract integration: real scanning, previews, copy and in-place moves.
Only native IPC and optional CLIP predictions are simulated. Run Vite first.
"""
import json
from pathlib import Path
import re
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
    (source / 'Kỳ nghỉ').mkdir()
    Image.new('RGB', (80, 60), 'green').save(source / 'forest.png')
    Image.new('RGB', (60, 80), 'blue').save(source / 'Kỳ nghỉ' / 'portrait.png')
    (source / 'trip.mp4').write_bytes(b'video fixture' * 1000)
    originals = {str(path.relative_to(source)): path.read_bytes() for path in source.rglob('*') if path.is_file()}
    browser = p.chromium.launch(executable_path=shutil.which('chromium'), args=['--no-sandbox'])
    page = browser.new_page(viewport={'width': 1280, 'height': 900})
    errors, operations = [], []
    page.on('pageerror', lambda error: errors.append(str(error)))

    def worker(request):
        operations.append(request['operation'])
        if request['operation'] == 'classify':
            assert all(not item['path'].endswith('.mp4') for item in request['photos'])
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
            if (command === 'plugin:dialog|open') return args.options.title.includes('bản sao') ? DESTINATION : SOURCE;
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
    page.get_by_role('button', name='Mở thư mục', exact=True).click()
    page.wait_for_function("document.querySelectorAll('.media-card').length === 3")
    assert {str(path.relative_to(source)): path.read_bytes() for path in source.rglob('*') if path.is_file()} == originals
    assert not list(source.glob('*.jsonl'))
    assert page.locator('.summary').inner_text().count('Video') == 1
    assert '3 file' in page.locator('.summary').inner_text()
    assert '12,7 KB' in page.locator('.summary').inner_text()
    # Filter/video recognition and multiple view modes.
    page.locator('.sidebar nav button').nth(2).click()
    assert page.locator('.media-card').count() == 1
    assert 'trip.mp4' in page.locator('.media-card').inner_text()
    assert '1 file · 12,7 KB' in page.locator('.library-meta').inner_text()
    page.get_by_role('checkbox', name='Chọn trip.mp4', exact=True).check()
    page.get_by_role('button', name='Xem trước sắp xếp').click()
    page.get_by_role('dialog').wait_for()
    assert page.locator('.plan-list tbody tr').count() == 1
    assert 'videos/trip.mp4' in page.locator('.plan-list').inner_text()
    page.keyboard.press('Escape')
    page.get_by_role('button', name='Bỏ chọn', exact=True).click()
    page.locator('.sidebar nav button').first.click()
    page.get_by_role('button', name='Danh sách', exact=True).click()
    assert page.locator('.table-scroll tbody tr').count() == 3
    page.get_by_role('combobox', name='Sắp xếp hiển thị').select_option('size-desc')
    assert 'trip.mp4' in page.locator('.table-scroll tbody tr').first.inner_text()
    page.get_by_role('button', name='Gọn', exact=True).click()
    assert page.locator('.media-grid.compact .media-card').count() == 3
    page.get_by_role('button', name='Thư mục', exact=True).click()
    page.get_by_role('button', name=re.compile('Kỳ nghỉ.*file')).click()
    assert page.locator('.table-scroll tbody tr').count() == 1
    page.get_by_role('button', name='Thư mục gốc', exact=True).click()
    page.get_by_role('button', name='Lưới', exact=True).click()
    # Preview is read-only; cancel changes nothing.
    page.get_by_role('button', name='Xem trước sắp xếp').click()
    page.get_by_role('dialog').wait_for()
    assert 'images/' in page.locator('.plan-list').inner_text()
    assert 'videos/' in page.locator('.plan-list').inner_text()
    assert not list(source.glob('*.jsonl'))
    page.get_by_role('button', name='Quay lại').click()
    assert not (source / 'images').exists()
    # Copy mode with AI fixtures + manual image category. Video must bypass AI.
    page.get_by_role('combobox', name='Thao tác sắp xếp').select_option('copy')
    page.get_by_role('combobox', name='Cách phân nhóm').select_option('content')
    page.get_by_role('button', name='Phân loại ảnh bằng AI').click()
    page.wait_for_function("document.querySelector('.activity').textContent.includes('Đã xử lý')")
    page.get_by_role('combobox', name='Nhóm của forest.png', exact=True).select_option('review')
    page.get_by_role('button', name='Chọn nơi lưu').click()
    page.get_by_role('button', name='Xem trước sắp xếp').click()
    page.get_by_role('dialog').wait_for()
    assert not list(destination.rglob('*.png'))
    page.get_by_role('button', name='Xác nhận sao chép').click()
    page.wait_for_function("document.querySelector('.activity').textContent.includes('Đã sao chép 3')")
    assert len(list(destination.rglob('*.png'))) == 2
    assert (destination / 'videos' / 'trip.mp4').exists()
    assert (destination / 'review' / 'forest.png').exists()
    assert {str(path.relative_to(source)): path.read_bytes() for path in source.rglob('*') if path.is_file()} == originals
    # Organize existing folder in place, no external destination. UI rescans automatically.
    page.get_by_role('combobox', name='Thao tác sắp xếp').select_option('inplace')
    page.get_by_role('combobox', name='Cách phân nhóm').select_option('type')
    page.get_by_role('button', name='Xem trước sắp xếp').click()
    page.get_by_role('dialog').wait_for()
    page.get_by_role('button', name='Xác nhận di chuyển').click()
    page.wait_for_function("document.querySelector('.activity').textContent.includes('Đã quét lại thư mục')")
    assert not (source / 'forest.png').exists()
    assert not (source / 'trip.mp4').exists()
    assert (source / 'images' / 'forest.png').read_bytes() == originals['forest.png']
    assert (source / 'videos' / 'trip.mp4').read_bytes() == originals['trip.mp4']
    assert page.locator('.media-card').count() == 3
    assert 'images' in page.locator('.media-grid').inner_text()
    assert 'videos' in page.locator('.media-grid').inner_text()
    assert '3 file' in page.locator('.summary').inner_text()
    # A second plan has no moves and cannot be confirmed.
    page.get_by_role('button', name='Xem trước sắp xếp').click()
    page.get_by_role('dialog').wait_for()
    assert page.get_by_role('button', name='Xác nhận di chuyển').is_disabled()
    page.keyboard.press('Escape')
    assert not page.get_by_role('dialog').is_visible()
    # Responsive layout: document stays inside the viewport, all controls remain reachable.
    for width in [360, 390, 768, 1280, 1600]:
        page.set_viewport_size({'width': width, 'height': 900})
        for label in ['Lưới', 'Gọn', 'Danh sách', 'Thư mục']:
            page.get_by_role('button', name=label, exact=True).click()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (width, label)
            assert page.get_by_role('button', name='Xem trước sắp xếp').is_visible()
        page.get_by_role('button', name='Lưới', exact=True).click()
        if width == 390:
            page.get_by_role('button', name='Xem trước sắp xếp').click()
            page.get_by_role('dialog').wait_for()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert page.get_by_role('button', name='Quay lại').is_visible()
            page.keyboard.press('Escape')
        if width in [390, 1280]:
            page.screenshot(path=f'/tmp/image-library-responsive-{width}.png', full_page=True)
    # Plain browser remains read-only and clearly identifies demo content.
    demo_page = browser.new_page(viewport={'width': 360, 'height': 900})
    demo_page.goto('http://127.0.0.1:1420/', wait_until='networkidle')
    demo_page.get_by_role('button', name='Xem minh họa').click()
    assert demo_page.locator('.media-card').count() == 6
    assert demo_page.get_by_role('button', name='Xem trước sắp xếp').is_disabled()
    assert 'Dữ liệu minh họa' in demo_page.locator('.activity').inner_text()
    assert demo_page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert not errors, errors
    assert operations.count('scan') >= 2
    browser.close()
    print('PASS: images/videos, byte totals, four views, folder navigation, read-only preview, AI fixture/manual groups, real copy and in-place move/rescan, idempotence, responsive widths 360–1600.')
