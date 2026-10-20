"""Bounded local attachments, stored separately from conversation JSON."""

import base64
import binascii
import io
import json
import uuid
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from pydantic import BaseModel, Field

MAX_BYTES = 10 * 1024 * 1024
MAX_TEXT = 200000


class AttachmentUpload(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    data: str = Field(max_length=14 * 1024 * 1024)


class Attachments:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def add(self, upload):
        try:
            raw = base64.b64decode(upload.data, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError("Некорректный файл") from error
        if not raw or len(raw) > MAX_BYTES:
            raise ValueError("Размер файла должен быть от 1 байта до 10 МБ")
        suffix = Path(upload.name).suffix.lower()
        record = {"id": uuid.uuid4().hex, "name": upload.name, "size": len(raw)}
        signatures = {".png": (b"\x89PNG\r\n\x1a\n", "image/png"),
                      ".jpg": (b"\xff\xd8\xff", "image/jpeg"), ".jpeg": (b"\xff\xd8\xff", "image/jpeg"),
                      ".webp": (b"RIFF", "image/webp")}
        if suffix in signatures:
            magic, mime = signatures[suffix]
            if not raw.startswith(magic) or suffix == ".webp" and raw[8:12] != b"WEBP":
                raise ValueError("Некорректный формат изображения")
            record.update(kind="image", data_url=f"data:{mime};base64," + upload.data)
        elif suffix == ".pdf":
            from pypdf import PdfReader
            from pypdf.errors import PdfReadError
            try:
                reader = PdfReader(io.BytesIO(raw))
                if reader.is_encrypted:
                    raise ValueError("PDF защищён паролем")
                text = ""
                for page in reader.pages:
                    text += (page.extract_text() or "") + "\n"
                    if len(text) > MAX_TEXT:
                        raise ValueError("Документ слишком большой: максимум 200 000 символов")
            except PdfReadError as error:
                raise ValueError("Не удалось прочитать PDF") from error
            record.update(kind="text", text=text)
        elif suffix == ".docx":
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    entry = archive.getinfo("word/document.xml")
                    if entry.file_size > 4 * MAX_TEXT:
                        raise ValueError("Документ слишком большой")
                    document = ElementTree.fromstring(archive.read(entry))
                    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
                    text = "\n".join("".join(p.itertext()) for p in document.iter(ns + "p"))
            except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as error:
                raise ValueError("Не удалось прочитать DOCX") from error
            record.update(kind="text", text=text)
        else:
            if b"\x00" in raw:
                raise ValueError("Поддерживаются изображения, PDF, DOCX и текстовые файлы UTF-8")
            try:
                record.update(kind="text", text=raw.decode("utf-8-sig"))
            except UnicodeDecodeError as error:
                raise ValueError("Текстовый файл должен быть в UTF-8") from error
        if record["kind"] == "text" and (not record["text"].strip() or len(record["text"]) > MAX_TEXT):
            raise ValueError("Нет доступного текста или превышен лимит 200 000 символов. Для сканов используйте изображения.")
        (self.directory / (record["id"] + ".json")).write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        return self.summary(record)

    @staticmethod
    def summary(record):
        return {key: record[key] for key in ("id", "name", "size", "kind")}

    def get(self, identifier):
        if len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
            raise ValueError("Некорректный идентификатор файла")
        try:
            return json.loads((self.directory / (identifier + ".json")).read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise ValueError("Прикреплённый файл не найден") from error

    def context(self, message, *, responses=False):
        text = message["content"]
        images = []
        for item in message.get("attachments", []):
            record = self.get(item["id"])
            if record["kind"] == "text":
                text += f"\n\n--- {record['name']} ---\n{record['text']}\n---"
            else:
                images.append(record["data_url"])
        if not images and not responses:
            return text
        parts = [{"type": "input_text" if responses else "text", "text": text}] if text else []
        for url in images:
            parts.append({"type": "input_image", "image_url": url} if responses
                         else {"type": "image_url", "image_url": {"url": url}})
        return parts
