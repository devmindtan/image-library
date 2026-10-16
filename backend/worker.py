"""JSON-lines worker. Only stdout is protocol; model diagnostics go to stderr."""
import base64
import io
import json
import os
from pathlib import Path
import shutil
import sys
import subprocess
import tempfile
import uuid

from PIL import Image, ImageOps

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff'}
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.mkv', '.avi', '.webm', '.m4v', '.mpeg', '.mpg', '.mts', '.m2ts', '.3gp'}
EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS
CATEGORIES = {
    'landscape': 'a landscape photograph of natural scenery, mountains, sea or forest',
    'portrait': 'a portrait photograph of a person or people',
    'document': 'a scanned document, printed text, receipt or handwritten page',
    'animals': 'a photograph of an animal or pet',
    'vehicles': 'a photograph of a car, motorcycle, bicycle or other vehicle',
    'food': 'a photograph of food or a meal',
    'architecture': 'a photograph of a building or architecture',
}
MODEL_ID = 'openai/clip-vit-base-patch32'


def emit(event):
    print(json.dumps(event, ensure_ascii=False), flush=True)


def root_path(value):
    root = Path(value).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError('Thư mục nguồn không hợp lệ')
    return root


def source_path(root, relative):
    rel = Path(relative)
    if rel.is_absolute() or not rel.parts or any(p in ('.', '..') for p in rel.parts):
        raise ValueError('Đường dẫn file không hợp lệ')
    candidate = root
    for part in rel.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise ValueError('Không xử lý liên kết tượng trưng')
    path = candidate.resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file() or path.suffix.lower() not in EXTENSIONS:
        raise ValueError('File phải nằm trong thư mục nguồn')
    return path


def fingerprint(path):
    stat = path.stat()
    return f'{stat.st_size}:{stat.st_mtime_ns}'


def thumbnail(path):
    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image)
        image.thumbnail((320, 240))
        out = io.BytesIO()
        image.convert('RGB').save(out, format='JPEG', quality=75)
        return 'data:image/jpeg;base64,' + base64.b64encode(out.getvalue()).decode('ascii')


def media_kind(path):
    return 'video' if path.suffix.lower() in VIDEO_EXTENSIONS else 'image'


def scan(root, send=emit):
    count = total_bytes = image_count = video_count = 0
    def walk_error(error):
        send({'type': 'warning', 'message': str(error)})
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
        dirs[:] = sorted(d for d in dirs if not (Path(directory) / d).is_symlink())
        for name in sorted(files):
            path = Path(directory) / name
            if path.is_symlink() or path.suffix.lower() not in EXTENSIONS:
                continue
            relative = path.relative_to(root)
            try:
                stamp = fingerprint(path)
                kind = media_kind(path)
                size = path.stat().st_size
                thumb = ''
                preview_error = None
                if kind == 'image':
                    try:
                        thumb = thumbnail(path)
                    except Exception as error:
                        preview_error = str(error)
                        send({'type': 'warning', 'message': f'{relative}: không tạo được ảnh xem trước: {error}'})
                if fingerprint(path) != stamp:
                    raise ValueError('File thay đổi khi đọc; hãy quét lại')
                category = 'videos' if kind == 'video' else None
                if kind == 'image' and len(relative.parts) > 1 and relative.parts[0] in {*CATEGORIES, 'review', 'images'}:
                    category = relative.parts[0]
                send({'type': 'photo', 'photo': {'path': str(relative), 'stamp': stamp, 'thumbnail': thumb,
                      'kind': kind, 'size': size, 'modified': path.stat().st_mtime,
                      'category': category, 'previewError': preview_error}})
                count += 1
                total_bytes += size
                image_count += kind == 'image'
                video_count += kind == 'video'
            except Exception as error:
                send({'type': 'warning', 'message': f'{relative}: {error}'})
    send({'type': 'done', 'count': count, 'bytes': total_bytes, 'images': image_count, 'videos': video_count})


class ClipClassifier:
    def __init__(self):
        import torch
        from transformers import CLIPModel, CLIPProcessor
        self.torch = torch
        self.processor = CLIPProcessor.from_pretrained(MODEL_ID)
        self.model = CLIPModel.from_pretrained(MODEL_ID, use_safetensors=True).eval()
        self.keys = list(CATEGORIES)
        with torch.inference_mode():
            text = self.processor(text=list(CATEGORIES.values()), return_tensors='pt', padding=True)
            self.text_features = self.model.get_text_features(**text)
            self.text_features /= self.text_features.norm(dim=-1, keepdim=True)

    def predict(self, path):
        with Image.open(path) as image:
            inputs = self.processor(images=ImageOps.exif_transpose(image).convert('RGB'), return_tensors='pt')
        with self.torch.inference_mode():
            features = self.model.get_image_features(**inputs)
            features /= features.norm(dim=-1, keepdim=True)
            scores = (self.model.logit_scale.exp() * features @ self.text_features.T).softmax(dim=-1)[0]
        index = int(scores.argmax())
        return self.keys[index], float(scores[index])


def classify(root, photos, threshold, send=emit, classifier=None):
    if not 0 <= threshold <= 1:
        raise ValueError('Ngưỡng phải nằm trong khoảng 0–1')
    if not photos:
        raise ValueError('Chưa có ảnh để phân loại')
    model = classifier
    for index, photo in enumerate(photos):
        try:
            path = source_path(root, photo['path'])
            if fingerprint(path) != photo['stamp']:
                raise ValueError('Ảnh đã thay đổi; hãy quét lại')
            if media_kind(path) == 'video':
                send({'type': 'classified', 'path': photo['path'], 'category': 'videos'})
            else:
                if model is None:
                    send({'type': 'status', 'message': 'Đang nạp CLIP (lần đầu cần tải mô hình)…'})
                    try:
                        model = ClipClassifier()
                    except Exception as error:
                        raise RuntimeError(f'Không nạp được CLIP: {error}') from error
                category, score = model.predict(path)
                if fingerprint(path) != photo['stamp']:
                    raise ValueError('Ảnh thay đổi trong khi phân loại')
                send({'type': 'classified', 'path': photo['path'], 'category': category if score >= threshold else 'review', 'score': score})
        except RuntimeError:
            raise
        except Exception as error:
            send({'type': 'photo_error', 'path': photo['path'], 'message': str(error)})
        send({'type': 'progress', 'current': index + 1, 'total': len(photos)})
    send({'type': 'done', 'count': len(photos)})


def checked_destination(root, value, create=True):
    destination = Path(value).expanduser().resolve()
    if destination == root or destination.is_relative_to(root) or root.is_relative_to(destination):
        raise ValueError('Chọn thư mục đích riêng, không chứa hoặc nằm trong thư mục nguồn')
    if create:
        destination.mkdir(parents=True, exist_ok=True)
    return destination


def organization_plan(root, photos, mode, strategy, destination=None):
    """Read-only preview. Recomputed before execution to reject stale/edited plans."""
    if mode not in {'inplace', 'copy'} or strategy not in {'type', 'content'}:
        raise ValueError('Chế độ sắp xếp không hợp lệ')
    if not photos:
        raise ValueError('Không có file cần sắp xếp')
    target_root = root if mode == 'inplace' else checked_destination(root, destination, create=False)
    plan, reserved, seen = [], set(), set()
    for photo in photos:
        path = source_path(root, photo['path'])
        if path in seen:
            raise ValueError('Danh sách file trùng lặp')
        seen.add(path)
        if fingerprint(path) != photo['stamp']:
            raise ValueError(f'{photo["path"]}: file đã thay đổi; hãy quét lại')
        kind = media_kind(path)
        category = 'videos' if kind == 'video' else ('images' if strategy == 'type' else photo.get('category'))
        if category not in ({*CATEGORIES, 'review', 'images'} if kind == 'image' else {'videos'}):
            raise ValueError(f'{photo["path"]}: cần chọn nhóm ảnh hoặc chạy phân loại AI trước')
        folder = target_root / category
        if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
            raise ValueError(f'{folder}: nhóm đích phải là thư mục thực')
        if folder.resolve().parent != target_root:
            raise ValueError('Thư mục nhóm không hợp lệ')
        target = folder / path.name
        skip = mode == 'inplace' and path == target
        counter = 0
        while not skip and (target.exists() or target.is_symlink() or os.path.normcase(str(target)) in reserved):
            counter += 1
            target = folder / f'{path.stem} ({counter}){path.suffix}'
        reserved.add(os.path.normcase(str(target)))
        plan.append({'path': photo['path'], 'stamp': photo['stamp'], 'target': str(target.relative_to(target_root)),
                     'category': category, 'kind': kind, 'size': path.stat().st_size, 'action': 'skip' if skip else ('move' if mode == 'inplace' else 'copy')})
    return plan


def apply_organization(root, photos, mode, strategy, expected_plan, destination=None, send=emit):
    plan = organization_plan(root, photos, mode, strategy, destination)
    if plan != expected_plan:
        raise ValueError('Thư mục đã thay đổi sau khi xem trước. Hãy tạo lại bản xem trước.')
    target_root = root if mode == 'inplace' else checked_destination(root, destination)
    journal = target_root / f'image-library-{uuid.uuid4().hex}.jsonl'
    completed = skipped = 0
    with journal.open('x', encoding='utf-8') as log:
        def record(event):
            log.write(json.dumps(event, ensure_ascii=False) + '\n')
            log.flush()
            os.fsync(log.fileno())
        record({'type': 'session', 'mode': mode, 'source': str(root), 'destination': str(target_root)})
        send({'type': 'status', 'message': f'Nhật ký: {journal}'})
        for index, item in enumerate(plan):
            temp = None
            try:
                path = source_path(root, item['path'])
                if fingerprint(path) != item['stamp']:
                    raise ValueError('File đã thay đổi; hãy quét lại')
                if item['action'] == 'skip':
                    skipped += 1
                    continue
                target = target_root / item['target']
                folder = target.parent
                if folder.is_symlink() or folder.resolve().parent != target_root:
                    raise ValueError('Thư mục đích đã thay đổi')
                folder.mkdir(exist_ok=True)
                record({'type': 'pending', 'source': str(path), 'destination': str(target), 'action': item['action']})
                if mode == 'inplace':
                    # Link then unlink: cancellation can leave duplicates, never remove the only copy.
                    os.link(path, target)
                    record({'type': 'linked', 'source': str(path), 'destination': str(target)})
                    if fingerprint(path) != item['stamp']:
                        raise ValueError('File thay đổi khi sắp xếp; giữ cả hai đường dẫn để kiểm tra')
                    path.unlink()
                    record({'type': 'moved', 'source': str(path), 'destination': str(target)})
                    send({'type': 'moved', 'path': item['path'], 'newPath': item['target'], 'category': item['category'], 'stamp': fingerprint(target)})
                else:
                    with tempfile.NamedTemporaryFile(dir=folder, prefix='.image-library-', suffix='.partial', delete=False) as stream:
                        temp = Path(stream.name)
                        record({'type': 'temporary', 'path': str(temp)})
                        with path.open('rb') as original:
                            shutil.copyfileobj(original, stream)
                        stream.flush()
                        os.fsync(stream.fileno())
                    if fingerprint(path) != item['stamp']:
                        raise ValueError('File thay đổi khi sao chép')
                    shutil.copystat(path, temp)
                    os.link(temp, target)
                    record({'type': 'copied', 'source': str(path), 'destination': str(target)})
                    send({'type': 'copied', 'path': item['path'], 'destination': str(target)})
                completed += 1
            except Exception as error:
                record({'type': 'error', 'path': item['path'], 'message': str(error)})
                send({'type': 'photo_error', 'path': item['path'], 'message': str(error)})
            finally:
                if temp is not None:
                    temp.unlink(missing_ok=True)
                send({'type': 'progress', 'current': index + 1, 'total': len(plan)})
    send({'type': 'done', 'count': completed, 'skipped': skipped, 'journal': str(journal)})



def metadata(root, photo):
    path = source_path(root, photo['path'])
    if fingerprint(path) != photo['stamp']:
        raise ValueError('File đã thay đổi; hãy quét lại')
    stat = path.stat()
    detail = {'general': {'Tên file': path.name, 'Đường dẫn': str(path), 'Loại': media_kind(path),
                         'Dung lượng (bytes)': stat.st_size, 'Sửa đổi (Unix)': stat.st_mtime}, 'warnings': []}
    if media_kind(path) == 'image':
        try:
            with Image.open(path) as image:
                detail['image'] = {'Định dạng': image.format, 'Chiều rộng': image.width, 'Chiều cao': image.height,
                                   'Chế độ màu': image.mode, 'Số khung': getattr(image, 'n_frames', 1)}
                from PIL.ExifTags import TAGS, GPSTAGS
                exif = image.getexif()
                detail['exif'] = {TAGS.get(key, str(key)): str(value)[:2000] for key, value in exif.items() if key not in (34665, 34853)}
                for tag, labels in [(34665, TAGS), (34853, GPSTAGS)]:
                    try:
                        for key, value in exif.get_ifd(tag).items():
                            detail['exif'][('GPS ' if tag == 34853 else '') + labels.get(key, str(key))] = str(value)[:2000]
                    except (KeyError, TypeError, ValueError):
                        pass
        except Exception as error:
            detail['warnings'].append('Không đọc được metadata ảnh: ' + str(error))
    else:
        try:
            probe = subprocess.run(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)], capture_output=True, text=True, timeout=15)
            if probe.returncode:
                raise ValueError('ffprobe không đọc được video hoặc định dạng chưa hỗ trợ')
            data = json.loads(probe.stdout)
            fmt = data.get('format', {})
            detail['video'] = {key: fmt[key] for key in ('format_name', 'duration', 'bit_rate', 'size', 'tags') if key in fmt}
            detail['streams'] = [{key: stream[key] for key in ('codec_type', 'codec_name', 'width', 'height', 'avg_frame_rate', 'sample_rate', 'channels', 'tags') if key in stream} for stream in data.get('streams', [])]
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError) as error:
            detail['warnings'].append('Cần ffprobe (FFmpeg) để đọc metadata video: ' + str(error))
    if fingerprint(path) != photo['stamp']:
        raise ValueError('File thay đổi khi đọc metadata; hãy quét lại')
    return detail


def trash_files(root, photos, send=emit, trash=None):
    if not photos:
        raise ValueError('Chưa chọn file để xóa')
    # Validate every entry before changing any file. Never fall back to permanent deletion.
    paths = []
    for photo in photos:
        path = source_path(root, photo['path'])
        if path in paths or fingerprint(path) != photo['stamp']:
            raise ValueError('Danh sách trùng hoặc file đã thay đổi; hãy quét lại')
        paths.append(path)
    if trash is None:
        from send2trash import send2trash
        trash = send2trash
    journal = root / f'image-library-trash-{uuid.uuid4().hex}.jsonl'
    count = 0
    with journal.open('x', encoding='utf-8') as log:
        def record(event):
            log.write(json.dumps(event, ensure_ascii=False) + '\n')
            log.flush()
            os.fsync(log.fileno())
        for index, (photo, path) in enumerate(zip(photos, paths)):
            try:
                if fingerprint(path) != photo['stamp']:
                    raise ValueError('File đã thay đổi; hãy quét lại')
                record({'type': 'pending_trash', 'path': str(path)})
                trash(str(path))
                record({'type': 'trashed', 'path': str(path)})
                send({'type': 'trashed', 'path': photo['path']})
                count += 1
            except Exception as error:
                record({'type': 'error', 'path': str(path), 'message': str(error)})
                send({'type': 'photo_error', 'path': photo['path'], 'message': str(error)})
            send({'type': 'progress', 'current': index + 1, 'total': len(paths)})
    send({'type': 'done', 'count': count, 'journal': str(journal)})


def main():
    try:
        request = json.loads(sys.stdin.readline())
        root = root_path(request['source'])
        operation = request['operation']
        if operation == 'scan':
            scan(root)
        elif operation == 'metadata':
            emit({'type': 'metadata', 'detail': metadata(root, request['photos'][0])})
            emit({'type': 'done', 'count': 1})
        elif operation == 'trash':
            trash_files(root, request['photos'])
        elif operation == 'classify':
            classify(root, request['photos'], float(request.get('threshold', .55)))
        elif operation == 'plan':
            plan = organization_plan(root, request['photos'], request['mode'], request['strategy'], request.get('destination'))
            emit({'type': 'plan', 'plan': plan})
            emit({'type': 'done', 'count': len(plan)})
        elif operation == 'apply':
            apply_organization(root, request['photos'], request['mode'], request['strategy'], request['plan'], request.get('destination'))
        else:
            raise ValueError('Thao tác không được hỗ trợ')
    except Exception as error:
        emit({'type': 'error', 'message': str(error)})
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
