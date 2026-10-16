"""JSON-lines worker. Only stdout is protocol; model diagnostics go to stderr."""
import base64
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid

from PIL import Image, ImageOps

EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff'}
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
        raise ValueError('Đường dẫn ảnh không hợp lệ')
    candidate = root
    for part in rel.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise ValueError('Không xử lý liên kết tượng trưng')
    path = candidate.resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file() or path.suffix.lower() not in EXTENSIONS:
        raise ValueError('Ảnh phải nằm trong thư mục nguồn')
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


def scan(root, send=emit):
    count = 0
    def walk_error(error):
        send({'type': 'warning', 'message': str(error)})
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
        dirs[:] = sorted(d for d in dirs if not (Path(directory) / d).is_symlink())
        for name in sorted(files):
            path = Path(directory) / name
            if path.is_symlink() or path.suffix.lower() not in EXTENSIONS:
                continue
            relative = str(path.relative_to(root))
            try:
                stamp = fingerprint(path)
                thumb = thumbnail(path)
                if fingerprint(path) != stamp:
                    raise ValueError('Ảnh thay đổi khi đọc; hãy quét lại')
                send({'type': 'photo', 'photo': {'path': relative, 'stamp': stamp, 'thumbnail': thumb}})
                count += 1
            except Exception as error:
                send({'type': 'warning', 'message': f'{relative}: {error}'})
    send({'type': 'done', 'count': count})


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
    send({'type': 'status', 'message': 'Đang nạp CLIP (lần đầu cần tải mô hình)…'})
    model = classifier if classifier is not None else ClipClassifier()
    for index, photo in enumerate(photos):
        try:
            path = source_path(root, photo['path'])
            if fingerprint(path) != photo['stamp']:
                raise ValueError('Ảnh đã thay đổi; hãy quét lại')
            category, score = model.predict(path)
            if fingerprint(path) != photo['stamp']:
                raise ValueError('Ảnh thay đổi trong khi phân loại')
            send({'type': 'classified', 'path': photo['path'], 'category': category if score >= threshold else 'review', 'score': score})
        except Exception as error:
            send({'type': 'photo_error', 'path': photo['path'], 'message': str(error)})
        send({'type': 'progress', 'current': index + 1, 'total': len(photos)})
    send({'type': 'done', 'count': len(photos)})


def checked_destination(root, value):
    destination = Path(value).expanduser().resolve()
    if destination == root or destination.is_relative_to(root) or root.is_relative_to(destination):
        raise ValueError('Chọn thư mục đích riêng, không chứa hoặc nằm trong thư mục nguồn')
    destination.mkdir(parents=True, exist_ok=True)
    return destination


def copy_photos(root, destination, photos, send=emit):
    if not photos:
        raise ValueError('Không có ảnh đã phân loại để sao chép')
    destination = checked_destination(root, destination)
    journal = destination / f'image-library-{uuid.uuid4().hex}.jsonl'
    copied = 0
    with journal.open('x', encoding='utf-8') as log:
        def record(entry):
            log.write(json.dumps(entry, ensure_ascii=False) + '\n')
            log.flush()
            os.fsync(log.fileno())
        record({'type': 'session', 'source': str(root), 'destination': str(destination)})
        send({'type': 'status', 'message': f'Nhật ký: {journal}'})
        for index, photo in enumerate(photos):
            temp = None
            try:
                path = source_path(root, photo['path'])
                if fingerprint(path) != photo['stamp']:
                    raise ValueError('Ảnh đã thay đổi; hãy quét và phân loại lại')
                category = photo['category']
                if category not in {*CATEGORIES, 'review'}:
                    raise ValueError('Nhóm không hợp lệ')
                folder = destination / category
                if folder.is_symlink():
                    raise ValueError('Thư mục đích không được là liên kết tượng trưng')
                folder.mkdir(exist_ok=True)
                if folder.resolve().parent != destination:
                    raise ValueError('Thư mục đích không hợp lệ')
                with tempfile.NamedTemporaryFile(dir=folder, prefix='.image-library-', suffix='.partial', delete=False) as stream:
                    temp = Path(stream.name)
                    record({'type': 'pending', 'source': str(path), 'temporary': str(temp)})
                    with path.open('rb') as source:
                        shutil.copyfileobj(source, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                if fingerprint(path) != photo['stamp']:
                    raise ValueError('Ảnh thay đổi trong khi sao chép')
                shutil.copystat(path, temp)
                counter = 0
                while True:
                    target = folder / (path.name if counter == 0 else f'{path.stem} ({counter}){path.suffix}')
                    record({'type': 'publishing', 'source': str(path), 'destination': str(target), 'temporary': str(temp)})
                    try:
                        # Atomic, exclusive publication: never overwrite an existing file.
                        os.link(temp, target)
                        break
                    except FileExistsError:
                        counter += 1
                temp.unlink()
                record({'type': 'copied', 'source': str(path), 'destination': str(target)})
                copied += 1
                send({'type': 'copied', 'path': photo['path'], 'destination': str(target)})
            except Exception as error:
                record({'type': 'error', 'path': photo.get('path'), 'message': str(error)})
                send({'type': 'photo_error', 'path': photo.get('path'), 'message': str(error)})
            finally:
                if temp is not None:
                    temp.unlink(missing_ok=True)
            send({'type': 'progress', 'current': index + 1, 'total': len(photos)})
    send({'type': 'done', 'count': copied, 'journal': str(journal)})


def main():
    try:
        request = json.loads(sys.stdin.readline())
        root = root_path(request['source'])
        operation = request['operation']
        if operation == 'scan':
            scan(root)
        elif operation == 'classify':
            classify(root, request['photos'], float(request.get('threshold', .55)))
        elif operation == 'copy':
            copy_photos(root, request['destination'], request['photos'])
        else:
            raise ValueError('Thao tác không được hỗ trợ')
    except Exception as error:
        emit({'type': 'error', 'message': str(error)})
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
