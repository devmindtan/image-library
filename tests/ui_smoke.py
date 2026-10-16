"""UI/IPC contract integration: real scanning, previews, copy and in-place moves.
Only native IPC and optional CLIP predictions are simulated. Run Vite first.
"""
import json
import os
from urllib.parse import unquote
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
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=blue:s=96x64:r=12','-t','1','-c:v','libvpx','-b:v','100k', str(source/'trip.webm')], check=True)
    video_bytes=(source/'trip.webm').stat().st_size
    video_size=(f'{video_bytes/1024:.1f}'.replace('.',',').removesuffix(',0')+' KB') if video_bytes>=1024 else f'{video_bytes} B'
    trash_home=Path(directory)/'trash-home'
    worker_env=os.environ.copy()
    worker_env['XDG_DATA_HOME']=str(trash_home)
    worker_python=str(REPO/'.venv/bin/python') if (REPO/'.venv/bin/python').exists() else sys.executable
    originals = {str(path.relative_to(source)): path.read_bytes() for path in source.rglob('*') if path.is_file()}
    browser = p.chromium.launch(executable_path=shutil.which('chromium'), args=['--no-sandbox'])
    page = browser.new_page(viewport={'width': 1280, 'height': 900})
    errors, operations = [], []
    page.on('pageerror', lambda error: errors.append(str(error)))

    def worker(request):
        operations.append(request['operation'])
        if request['operation'] == 'classify':
            assert all(not item['path'].endswith('.webm') for item in request['photos'])
            return [{'type': 'classified', 'path': photo['path'], 'category': 'landscape', 'score': .8}
                    for photo in request['photos']] + [{'type': 'done', 'count': len(request['photos'])}]
        command=[worker_python, str(REPO / 'backend/worker.py')]
        if request['operation']=='trash':
            # The cloud HOME and /tmp use different devices. Pin the device probe in
            # this fixture to exercise the real Freedesktop trash in our isolated XDG root.
            harness="import sys,os,runpy,send2trash; import send2trash.plat_other as p; p.get_dev=lambda _:os.stat(sys.argv[2]).st_dev; send2trash.send2trash=p.send2trash; runpy.run_path(sys.argv[1],run_name='__main__')"
            command=[worker_python,'-c',harness,str(REPO/'backend/worker.py'),str(source)]
        result = subprocess.run(command, input=json.dumps(request), text=True, capture_output=True, env=worker_env)
        assert result.returncode == 0, result.stdout + result.stderr
        return [json.loads(line) for line in result.stdout.splitlines()]

    page.expose_function('testWorker', worker)
    page.add_init_script('''
        window.isTauri = true;
        window.__TAURI_INTERNALS__ = {
          transformCallback: () => 1, unregisterCallback: () => {},
          convertFileSrc: (path) => 'http://media.test/' + encodeURIComponent(path),
          invoke: async (command, args) => {
            if (command === 'plugin:dialog|open') return args.options.title.includes('bản sao') ? DESTINATION : SOURCE;
            if (command === 'run_worker') {
              for (const event of await window.testWorker(args.request)) args.onEvent.onmessage(event);
              return;
            }
            if (command === 'authorize_media') return SOURCE + '/' + args.path;
            if (command === 'cancel_worker') return;
            throw Error('Unexpected command: ' + command);
          }
        };
    '''.replace('SOURCE', json.dumps(str(source))).replace('DESTINATION', json.dumps(str(destination))))
    def serve_media(route):
        path=Path(unquote(route.request.url.removeprefix('http://media.test/')))
        assert path.resolve().is_relative_to(source)
        data=path.read_bytes()
        headers={'Access-Control-Allow-Origin':'*','Accept-Ranges':'bytes'}
        content_type='video/webm' if path.suffix=='.webm' else 'image/png'
        requested=route.request.headers.get('range')
        if requested:
            start,end=requested.removeprefix('bytes=').split('-')
            start=int(start or '0'); end=min(int(end) if end else len(data)-1,len(data)-1)
            headers['Content-Range']=f'bytes {start}-{end}/{len(data)}'
            route.fulfill(status=206,body=data[start:end+1],content_type=content_type,headers=headers)
        else:
            route.fulfill(body=data,content_type=content_type,headers=headers)
    page.route('http://media.test/**',serve_media)
    page.goto('http://127.0.0.1:1420/', wait_until='networkidle')
    page.get_by_role('button', name='Mở thư mục', exact=True).click()
    page.wait_for_function("document.querySelectorAll('.media-card').length === 3")
    assert {str(path.relative_to(source)): path.read_bytes() for path in source.rglob('*') if path.is_file()} == originals
    assert not list(source.glob('*.jsonl'))
    assert page.locator('.summary').inner_text().count('Video') == 1
    assert '3 file' in page.locator('.summary').inner_text()
    assert video_size in page.locator('.summary').inner_text()
    # Original image, zoom and actual image/video metadata from worker.
    page.get_by_role('button',name='Mở forest.png',exact=True).click()
    page.wait_for_function("document.querySelector('.metadata').textContent.includes('Chiều rộng')")
    page.wait_for_function("document.querySelector('.viewer-media img')?.naturalWidth === 80")
    page.get_by_role('button',name='Phóng to',exact=True).click()
    assert '125%' in page.locator('.viewer-controls').inner_text()
    page.set_viewport_size({'width':390,'height':900})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.set_viewport_size({'width':1280,'height':900})
    page.get_by_role('button',name='Đóng',exact=True).click()
    page.get_by_role('button',name='Mở trip.webm',exact=True).click()
    page.wait_for_function("document.querySelector('video')?.readyState >= 2")
    page.locator('video').evaluate('(video)=>video.play()')
    page.wait_for_function("document.querySelector('video').currentTime > 0")
    page.locator('video').evaluate('(video)=>{video.pause();video.currentTime=.5;}')
    page.wait_for_function("Math.abs(document.querySelector('video').currentTime-.5)<.1")
    page.wait_for_function("document.querySelector('.metadata').textContent.includes('vp8')")
    assert '96' in page.locator('.metadata').inner_text()
    page.get_by_role('button',name='Đóng',exact=True).click()
    assert page.locator('video').count()==0
    # AI remains the default workflow and visible without changing any setting.
    assert page.get_by_role('combobox', name='Cách phân nhóm').input_value() == 'content'
    assert page.get_by_role('button', name='Phân loại ảnh bằng AI').is_visible()
    page.get_by_role('button', name='Phân loại ảnh bằng AI').click()
    page.wait_for_function("document.querySelector('.activity').textContent.includes('Đã xử lý')")
    assert page.get_by_role('combobox', name='Nhóm của forest.png', exact=True).input_value() == 'landscape'
    page.get_by_role('combobox', name='Cách phân nhóm').select_option('type')
    assert page.get_by_role('button', name='Phân loại ảnh bằng AI').is_visible()
    # Filter/video recognition and multiple view modes.
    page.locator('.sidebar nav button').nth(2).click()
    assert page.locator('.media-card').count() == 1
    assert page.get_by_role('button', name='Phân loại ảnh bằng AI').is_disabled()
    assert 'trip.webm' in page.locator('.media-card').inner_text()
    assert f'1 file · {video_size}' in page.locator('.library-meta').inner_text()
    page.get_by_role('checkbox', name='Chọn trip.webm', exact=True).check()
    page.get_by_role('button', name='Xem trước sắp xếp').click()
    page.get_by_role('dialog').wait_for()
    assert page.locator('.plan-list tbody tr').count() == 1
    assert 'videos/trip.webm' in page.locator('.plan-list').inner_text()
    page.keyboard.press('Escape')
    page.get_by_role('button', name='Bỏ chọn', exact=True).click()
    page.locator('.sidebar nav button').first.click()
    page.get_by_role('button', name='Danh sách', exact=True).click()
    assert page.locator('.table-scroll tbody tr').count() == 3
    page.get_by_role('combobox', name='Sắp xếp hiển thị').select_option('size-desc')
    assert 'trip.webm' in page.locator('.table-scroll tbody tr').first.inner_text()
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
    page.get_by_role('button', name='Phân loại ảnh bằng AI').click()
    assert page.get_by_role('combobox', name='Cách phân nhóm').input_value() == 'content'
    page.wait_for_function("document.querySelector('.activity').textContent.includes('Đã xử lý')")
    page.get_by_role('combobox', name='Nhóm của forest.png', exact=True).select_option('review')
    page.get_by_role('button', name='Chọn nơi lưu').click()
    page.get_by_role('button', name='Xem trước sắp xếp').click()
    page.get_by_role('dialog').wait_for()
    assert not list(destination.rglob('*.png'))
    page.get_by_role('button', name='Xác nhận sao chép').click()
    page.wait_for_function("document.querySelector('.activity').textContent.includes('Đã sao chép 3')")
    assert len(list(destination.rglob('*.png'))) == 2
    assert (destination / 'videos' / 'trip.webm').exists()
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
    assert not (source / 'trip.webm').exists()
    assert (source / 'images' / 'forest.png').read_bytes() == originals['forest.png']
    assert (source / 'videos' / 'trip.webm').read_bytes() == originals['trip.webm']
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
    # Deletion requires confirmation; actual Send2Trash uses an isolated Linux trash root.
    page.get_by_role('button',name='Mở images/forest.png',exact=True).click()
    page.wait_for_function("document.querySelector('.metadata').textContent.includes('Chiều rộng')")
    page.get_by_role('button',name='Xóa file này',exact=True).click()
    page.get_by_role('dialog',name='Chuyển 1 file vào Thùng rác?').wait_for()
    page.get_by_role('button',name='Giữ lại',exact=True).click()
    assert (source/'images/forest.png').exists()
    page.get_by_role('checkbox',name='Chọn images/forest.png',exact=True).check()
    page.get_by_role('checkbox',name='Chọn videos/trip.webm',exact=True).check()
    page.get_by_role('button',name='Xóa 2 file đã chọn',exact=True).click()
    page.get_by_role('button',name='Xác nhận chuyển vào Thùng rác',exact=True).click()
    page.wait_for_function("document.querySelector('.activity').textContent.includes('Đã chuyển 2 file')")
    assert not (source/'images/forest.png').exists()
    assert not (source/'videos/trip.webm').exists()
    assert page.locator('.media-card').count()==1
    assert (trash_home/'Trash/files/forest.png').read_bytes()==originals['forest.png']
    assert (trash_home/'Trash/files/trip.webm').read_bytes()==originals['trip.webm']
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
    print('PASS: original-image zoom/metadata, real WebM playback/ffprobe metadata, confirmed recoverable trash, AI fixtures, images/videos, sizes, four views, real organization, responsive widths 360–1600.')
