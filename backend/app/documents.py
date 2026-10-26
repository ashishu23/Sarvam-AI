import hashlib
import io
import json
import warnings

import mutagen
from fastapi import HTTPException
from PIL import Image, ImageFilter, ImageOps, ImageStat
from pypdf import PdfReader, PdfWriter

from .config import Settings
from .repository import uid

Image.MAX_IMAGE_PIXELS = 24_000_000


def inspect_file(data: bytes, filename: str, settings: Settings) -> dict:
    if not data or len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f'Each file must be nonempty and at most {settings.max_upload_mb} MB.')
    issues = []
    try:
        if data.startswith(b'%PDF-'):
            reader = PdfReader(io.BytesIO(data), strict=True)
            if reader.is_encrypted:
                raise HTTPException(422, 'Password-protected PDFs cannot be read. Upload an unlocked copy.')
            count = len(reader.pages)
            mime = 'application/pdf'
            if not 1 <= count <= settings.max_document_pages:
                raise HTTPException(422, f'Upload 1–{settings.max_document_pages} pages.')
        else:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                image = Image.open(io.BytesIO(data))
                if image.format not in ('JPEG', 'PNG', 'WEBP'):
                    raise HTTPException(415, 'Use JPEG, PNG, WebP or PDF.')
                image.load()
            mime = Image.MIME[image.format]
            count = 1
            image = ImageOps.exif_transpose(image).convert('L')
            if min(image.size) < 700:
                issues.append('Low resolution: small text may be unreadable. Consider a closer photograph.')
            thumb = image.copy()
            thumb.thumbnail((900, 900))
            spread = ImageStat.Stat(thumb).stddev[0]
            edge = ImageStat.Stat(thumb.filter(ImageFilter.FIND_EDGES)).var[0]
            if spread < 12:
                issues.append('This page may be empty, overexposed or unreadable. Replace it if text is missing.')
            elif edge < 100:
                issues.append('Possible blur: inspect names, amounts and dates before confirming.')
            issues.append('Check glare, cropped edges and sideways text in the preview; image checks cannot establish completeness.')
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(422, 'This file is damaged, too large to decode safely, or is not a supported PDF/image.') from None
    return {'id': uid(), 'filename': filename.replace('\\', '/').split('/')[-1][:160], 'mime': mime,
            'page_count': count, 'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data), 'issues': issues}


def prepare_document(files: list[tuple[bytes, dict]], manifest: str, settings: Settings) -> tuple[bytes, list[dict]]:
    try:
        selections = json.loads(manifest)
        if not isinstance(selections, list) or not 1 <= len(selections) <= settings.max_document_pages:
            raise ValueError()
        writer = PdfWriter()
        pages = []
        selected = set()
        for number, selection in enumerate(selections, start=1):
            index, page_number, rotation = selection['file_index'], selection['page'], selection.get('rotation', 0)
            if type(index) is not int or type(page_number) is not int or type(rotation) is not int:
                raise ValueError()
            if index < 0 or index >= len(files) or rotation not in (0, 90, 180, 270):
                raise ValueError()
            data, original = files[index]
            if not 1 <= page_number <= original['page_count'] or (index, page_number) in selected:
                raise ValueError()
            selected.add((index, page_number))
            if original['mime'] == 'application/pdf':
                page = PdfReader(io.BytesIO(data)).pages[page_number - 1]
            else:
                image = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert('RGB')
                buffer = io.BytesIO()
                image.save(buffer, format='PDF')
                page = PdfReader(io.BytesIO(buffer.getvalue())).pages[0]
            if rotation:
                page.rotate(rotation)
            writer.add_page(page)
            pages.append({'id': uid(), 'number': number, 'original_id': original['id'],
                          'original_page': page_number, 'rotation': rotation, 'issues': original['issues'].copy(),
                          'machine_text': '', 'confirmed_text': None, 'corrections': []})
        output = io.BytesIO()
        writer.write(output)
        if len(output.getvalue()) > 25 * 1024 * 1024:
            raise HTTPException(413, 'Prepared pages exceed 25 MB. Use smaller images or fewer pages.')
        return output.getvalue(), pages
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(422, 'Invalid page order or rotation. Refresh the previews and try again.') from None


def extracted_pages(result: dict, count: int) -> dict[int, str]:
    found = {}
    for document in result.get('documents', []):
        for page in document.get('pages') or []:
            number, content = page.get('page_number'), page.get('content')
            if type(number) is int and 1 <= number <= count and isinstance(content, str):
                found[number] = content[:40000]
    return found


def audio_duration(data: bytes, mime: str, limit: int) -> float:
    if len(data) > 4 * 1024 * 1024 or not data:
        raise HTTPException(413, 'Recording must be nonempty and at most 4 MB.')
    if mime == 'audio/wav':
        import wave
        try:
            with wave.open(io.BytesIO(data)) as audio:
                duration = audio.getnframes() / audio.getframerate()
        except Exception:
            raise HTTPException(422, 'The WAV recording could not be read.') from None
    else:
        try:
            audio = mutagen.File(io.BytesIO(data))
            duration = float(audio.info.length)
        except Exception:
            raise HTTPException(422, 'Recording duration could not be verified. Use the built-in WAV recorder or type your question.') from None
    if not 0 < duration <= limit + 0.2:
        raise HTTPException(422, f'Record for no more than {limit} seconds.')
    return duration
