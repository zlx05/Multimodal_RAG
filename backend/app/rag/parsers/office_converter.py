"""Convert legacy Microsoft Office files to OOXML formats on Windows."""

from __future__ import annotations

import os
from pathlib import Path


LEGACY_EXTENSIONS = {".doc", ".ppt"}


def convert_legacy_office(
    document_id: str,
    path: str | Path,
    work_dir: str | Path | None = None,
) -> Path:
    """Convert .doc/.ppt through installed Word/PowerPoint COM automation.

    Conversion output is kept under the per-document work directory and is
    removed together with the document's ingestion artifacts.
    """
    source = Path(path)
    extension = source.suffix.lower()
    if extension not in LEGACY_EXTENSIONS:
        return source

    output_dir = Path(work_dir or os.getenv("RAG_WORK_DIR", "data/.work")) / document_id / "converted"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"{source.stem}{'.docx' if extension == '.doc' else '.pptx'}"

    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:
        raise RuntimeError(
            "解析 .doc/.ppt 需要 Windows Microsoft Office 和 pywin32；"
            "请安装 backend/requirements-multimodal.txt。"
        ) from exc

    pythoncom.CoInitialize()
    application = None
    document = None
    try:
        if extension == ".doc":
            application = win32com.client.DispatchEx("Word.Application")
            application.Visible = False
            application.DisplayAlerts = 0
            document = application.Documents.Open(
                str(source),
                ReadOnly=True,
                AddToRecentFiles=False,
                ConfirmConversions=False,
            )
            # wdFormatXMLDocument
            document.SaveAs2(str(output), FileFormat=16)
        else:
            application = win32com.client.DispatchEx("PowerPoint.Application")
            document = application.Presentations.Open(
                str(source),
                ReadOnly=True,
                Untitled=False,
                WithWindow=False,
            )
            # ppSaveAsOpenXMLPresentation
            document.SaveAs(str(output), 24)
    except Exception as exc:
        output.unlink(missing_ok=True)
        raise RuntimeError(f"无法将 {source.name} 转换为现代 Office 格式: {exc}") from exc
    finally:
        if document is not None:
            try:
                document.Close()
            except Exception:
                pass
        if application is not None:
            try:
                application.Quit()
            except Exception:
                pass
        pythoncom.CoUninitialize()

    return output
