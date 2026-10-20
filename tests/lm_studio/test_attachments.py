import base64
import io
import zipfile

import pytest

from comfyui_studio.lm_studio.attachments import Attachments, AttachmentUpload


def add(store, name, data):
    return store.add(AttachmentUpload(name=name, data=base64.b64encode(data).decode()))


def test_docx_text_is_extracted_without_running_document_content(tmp_path):
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>Test document</w:t></w:r></w:p></w:document>')
    store = Attachments(tmp_path)
    file = add(store, "document.docx", content.getvalue())
    assert store.get(file["id"])["text"] == "Test document"


@pytest.mark.parametrize("name,data", [("binary.exe", b"MZ\x00bad"), ("fake.png", b"not an image"),
                                       ("text.txt", b"\xff\xfe"), ("empty.txt", b""), ("doc.docx", b"not a zip")])
def test_invalid_files_are_rejected(tmp_path, name, data):
    directory = tmp_path / "attachments"
    with pytest.raises(ValueError):
        add(Attachments(directory), name, data)
    assert list(directory.iterdir()) == []


def test_attachment_path_cannot_escape_directory(tmp_path):
    with pytest.raises(ValueError):
        Attachments(tmp_path).get("../sensitive")


def test_pdf_text_layer_is_extracted(tmp_path):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 10 100 Td (PDF attachment text) Tj ET")
    page[NameObject("/Contents")] = stream
    output = io.BytesIO()
    writer.write(output)
    store = Attachments(tmp_path / "attachments")
    file = add(store, "notes.pdf", output.getvalue())
    assert store.get(file["id"])["text"].strip() == "PDF attachment text"
