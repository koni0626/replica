"""
Word(.docx) 読み取り用ツール

- 依存: python-docx
    pip install python-docx

- 役割:
  * 指定パスの .docx ファイルからテキストを抽出して返す
  * 1つの関数 read_docx_text(path, limit_chars=None) を公開
  * 将来の拡張（表/ヘッダ/脚注など）に備えて最小だが拡張しやすい構成

- 使用例:
    from tools.office_word_tool import read_docx_text
    text = read_docx_text(r"C:\\path\\to\\file.docx", limit_chars=5000)

注意:
- .doc (97-2003) 形式は対象外。必要なら別途 soffic e / tika などで変換してから使用してください。
"""
from __future__ import annotations

import os
from typing import Optional

try:
    from docx import Document  # python-docx
except Exception:  # ランタイム依存を緩く
    Document = None  # type: ignore


class WordReadError(Exception):
    """Word 読み取り時の一般例外"""


def _is_docx(path: str) -> bool:
    return os.path.splitext(path)[1].lower() == ".docx"


def read_docx_text(path: str, limit_chars: Optional[int] = None) -> str:
    """
    .docx から段落テキストを抽出して返す。

    Args:
        path: 対象ファイルの絶対 or 相対パス
        limit_chars: 返却する最大文字数（None なら上限なし）。超過時は末尾を切り詰め。

    Returns:
        抽出テキスト（失敗時は空文字）。

    Raises:
        WordReadError: 依存未導入 or ファイル不正など致命的なときに発生（呼び側で握りたい場合は捕捉）
    """
    if not _is_docx(path):
        raise WordReadError(f"Unsupported extension for Word reader: {path}")

    if Document is None:
        # 依存がない環境では例外
        raise WordReadError(
            "python-docx is not installed. Please `pip install python-docx`.")

    if not os.path.exists(path):
        raise WordReadError(f"File not found: {path}")

    try:
        doc = Document(path)
        parts = []
        # 段落
        for p in doc.paragraphs:
            txt = (p.text or "").strip("\n\r")
            if txt:
                parts.append(txt)
        # 表のセル（簡易版） — 必要最低限で取得（行順）
        for table in getattr(doc, 'tables', []) or []:
            for row in table.rows:
                row_text = []
                for cell in row.cells:
                    ctext = cell.text.strip() if cell and cell.text else ""
                    if ctext:
                        row_text.append(ctext)
                if row_text:
                    parts.append("\t".join(row_text))
        text = "\n".join(parts)
        if limit_chars is not None and limit_chars > 0 and len(text) > limit_chars:
            return text[:limit_chars]
        return text
    except Exception as e:
        # 解析に失敗しても致命でない場合は空文字を返す運用も可能だが、
        # ここでは原因追跡のため例外化。
        raise WordReadError(f"Failed to read docx: {e}") from e
