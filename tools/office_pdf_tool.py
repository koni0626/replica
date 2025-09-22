"""
PDF(.pdf) 読み取り用ツール

依存（いずれかが導入されていれば動作します。優先順に使用）:
- pdfplumber      （推奨・品質高め）
- pdfminer.six    （ページ単位抽出に対応）
- PyPDF2          （簡易的な抽出）

インストール例:
    pip install pdfplumber
    # or
    pip install pdfminer.six
    # or
    pip install PyPDF2

提供関数:
- read_pdf_text(path, limit_chars=None, max_pages=None,
                include_page_header=True, page_header_fmt="# Page {num}",
                page_separator="\n\n") -> str
    指定PDFからテキストを抽出して返します。
    可能ならページごとに抽出し、ヘッダを付与して結合します。

注意:
- スキャンPDF（画像のみ）はテキスト抽出できません（OCRが必要）。
- 暗号化PDFは空パスワードで復号を試みますが、失敗時は例外を送出します。
- .pdf 以外の拡張子は PdfReadError を送出します。
"""
from __future__ import annotations

import os
from typing import List, Optional

# 依存は任意（存在すれば使う）
try:
    import pdfplumber  # type: ignore
except Exception:  # pragma: no cover
    pdfplumber = None  # type: ignore

try:
    from pdfminer.high_level import extract_pages  # type: ignore
    from pdfminer.layout import LAParams, LTTextContainer  # type: ignore
except Exception:  # pragma: no cover
    extract_pages = None  # type: ignore
    LAParams = None  # type: ignore
    LTTextContainer = None  # type: ignore

try:
    import PyPDF2  # type: ignore
except Exception:  # pragma: no cover
    PyPDF2 = None  # type: ignore


class PdfReadError(Exception):
    """PDF 読み取り時の一般例外"""


def _is_pdf(path: str) -> bool:
    return os.path.splitext(path)[1].lower() == ".pdf"


def _truncate(text: str, limit_chars: Optional[int]) -> str:
    if limit_chars is not None and limit_chars > 0 and len(text) > limit_chars:
        return text[:limit_chars]
    return text


def _extract_with_pdfplumber(path: str, max_pages: Optional[int]) -> List[str]:
    pages: List[str] = []
    assert pdfplumber is not None
    with pdfplumber.open(path) as pdf:
        for idx, page in enumerate(pdf.pages):
            if max_pages is not None and idx >= max_pages:
                break
            try:
                txt = page.extract_text() or ""
            except Exception:
                txt = ""
            pages.append(txt.rstrip())
    return pages


def _extract_with_pdfminer(path: str, max_pages: Optional[int]) -> List[str]:
    pages_text: List[str] = []
    assert extract_pages is not None and LAParams is not None and LTTextContainer is not None
    laparams = LAParams()  # 既定のレイアウトパラメータ
    for idx, layout in enumerate(extract_pages(path, laparams=laparams)):
        if max_pages is not None and idx >= max_pages:
            break
        parts: List[str] = []
        try:
            for element in layout:
                if isinstance(element, LTTextContainer):
                    parts.append(element.get_text())
        except Exception:
            # ページ単位で失敗しても次へ
            pass
        pages_text.append("".join(parts).rstrip())
    return pages_text


def _extract_with_pypdf2(path: str, max_pages: Optional[int]) -> List[str]:
    assert PyPDF2 is not None
    pages: List[str] = []
    with open(path, "rb") as f:
        reader = PyPDF2.PdfReader(f)
        if reader.is_encrypted:
            try:
                reader.decrypt("")  # 空パスワード試行
            except Exception as e:
                raise PdfReadError(f"Encrypted PDF and decrypt failed: {e}") from e
        for idx, page in enumerate(reader.pages):
            if max_pages is not None and idx >= max_pages:
                break
            try:
                txt = page.extract_text() or ""
            except Exception:
                txt = ""
            pages.append(txt.rstrip())
    return pages


def read_pdf_text(
    path: str,
    limit_chars: Optional[int] = None,
    max_pages: Optional[int] = None,
    include_page_header: bool = True,
    page_header_fmt: str = "# Page {num}",
    page_separator: str = "\n\n",
) -> str:
    """
    PDF からテキストを抽出して返す。

    Args:
        path: 対象PDFの絶対 or 相対パス
        limit_chars: 返却する最大文字数（None なら上限なし）。超過時は末尾を切り詰め。
        max_pages: 最大ページ数（None なら全ページ）
        include_page_header: True の場合、各ページ先頭にヘッダ行を付与
        page_header_fmt: ヘッダのフォーマット（例: "# Page {num}")
        page_separator: ページ結合時の区切り文字

    Returns:
        抽出テキスト（失敗時は例外）

    Raises:
        PdfReadError: 依存未導入 / 拡張子不正 / 解析失敗 等
    """
    if not _is_pdf(path):
        raise PdfReadError(f"Unsupported extension for PDF reader: {path}")

    if not os.path.exists(path):
        raise PdfReadError(f"File not found: {path}")

    pages_text: List[str] = []
    last_error: Optional[Exception] = None

    # 1) pdfplumber
    if pdfplumber is not None:
        try:
            pages_text = _extract_with_pdfplumber(path, max_pages)
        except Exception as e:  # 続行可能
            last_error = e
            pages_text = []

    # 2) pdfminer.six（ページごと抽出）
    if not pages_text and extract_pages is not None and LAParams is not None and LTTextContainer is not None:
        try:
            pages_text = _extract_with_pdfminer(path, max_pages)
        except Exception as e:
            last_error = e
            pages_text = []

    # 3) PyPDF2
    if not pages_text and PyPDF2 is not None:
        try:
            pages_text = _extract_with_pypdf2(path, max_pages)
        except Exception as e:
            last_error = e
            pages_text = []

    if not pages_text:
        # 依存が全滅 or 失敗
        if last_error:
            raise PdfReadError(f"Failed to read PDF: {last_error}") from last_error
        raise PdfReadError("No available backend to read PDF (install pdfplumber or pdfminer.six or PyPDF2)")

    # ページ結合
    chunks: List[str] = []
    for i, page_text in enumerate(pages_text, start=1):
        if include_page_header:
            chunks.append(page_header_fmt.format(num=i))
        chunks.append(page_text)
    text = page_separator.join(chunks)

    return _truncate(text, limit_chars)


__all__ = [
    "PdfReadError",
    "read_pdf_text",
]
