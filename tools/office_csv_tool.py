"""
CSV(.csv) 出力用ツール（Windows Excel 互換）

- 目的:
  Windows の Excel で文字化けなく開ける CSV を簡単に出力する。
  既定文字コードは CP932（Shift_JIS 相当／Windows-31J）。行末は CRLF。

- 公開関数:
  write_csv_cp932(path, rows, headers=None, delimiter=",", quoting="minimal",
                  ensure_parent=True, encoding_errors="strict") -> str

- rows の受け付け形式:
  * list[dict]: ヘッダーは headers を優先。未指定なら最初の行のキー順を使用。
  * list[list] / list[tuple]: 二次元配列としてそのまま出力。headers を渡した場合は先頭行に追加。

- 注意:
  * CP932 で表現できない文字（例: 絵文字など）が含まれる場合、既定では UnicodeEncodeError になります。
    その場合は encoding_errors="replace" を指定すると � に置換されます（データ欠落の可能性に注意）。
  * Excel の CSV 読み込みは区切り文字が環境依存のケースがあります。既定はカンマ(,)です。

使用例:
    from tools.office_csv_tool import write_csv_cp932

    # 1) dict の配列から出力（ヘッダー自動）
    rows = [
        {"id": 1, "name": "山田太郎", "note": "テスト"},
        {"id": 2, "name": "鈴木花子", "note": "確認"},
    ]
    out = write_csv_cp932(r"C:\\tmp\\sample1.csv", rows)

    # 2) dict の配列＋明示ヘッダー順
    headers = ["id", "name", "note"]
    out = write_csv_cp932(r"C:\\tmp\\sample2.csv", rows, headers=headers)

    # 3) 二次元配列（ヘッダー付与）
    rows2 = [[1, "山田太郎", "テスト"], [2, "鈴木花子", "確認"]]
    out = write_csv_cp932("./sample3.csv", rows2, headers=["id", "name", "note"])
"""
from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Union, Dict, Any


class CsvWriteError(Exception):
    """CSV 書き出し時の一般例外"""


def _is_dict_rows(rows: Iterable[Any]) -> bool:
    """先頭要素を見て dict 群かどうかを推定（空なら False）。"""
    try:
        first = next(iter(rows))
    except StopIteration:
        return False
    return isinstance(first, dict)


def _iter_rows_copy(rows: Iterable[Any]) -> Iterable[Any]:
    """ジェネレータ等でも2度参照できるように一旦 list 化する。"""
    if isinstance(rows, list):
        return rows
    return list(rows)


def _resolve_headers_for_dict_rows(rows: List[Dict[str, Any]], headers: Optional[Sequence[str]]) -> List[str]:
    if headers:
        return list(headers)
    if not rows:
        return []
    # 1行目のキー順を採用（Order 保持を前提）
    return list(rows[0].keys())


def write_csv_cp932(
    path: str,
    rows: Iterable[Union[Sequence[Any], Dict[str, Any]]],
    headers: Optional[Sequence[str]] = None,
    delimiter: str = ",",
    quoting: Union[str, int] = "minimal",
    ensure_parent: bool = True,
    encoding_errors: str = "strict",
) -> str:
    """
    CSV を CP932 で書き出す（Windows Excel 互換）。

    Args:
        path: 出力先パス（絶対 or 相対）。親ディレクトリが無ければ ensure_parent=True で自動作成。
        rows: 行データ。list[dict] または list[list/tuple] を受け付け。
        headers: ヘッダー行。dict rows ではこれが優先。None の場合は先頭行のキー順。
        delimiter: 区切り文字（既定 ","）。
        quoting: クォート方針（"minimal"|"all"|"nonnumeric"|"none" または csv.QUOTE_*）。
        ensure_parent: True なら親ディレクトリを自動作成。
        encoding_errors: 文字エンコードエラー時の方針（"strict"|"replace"|"ignore"）。

    Returns:
        書き込んだファイルの絶対パス文字列。

    Raises:
        CsvWriteError: パラメータ不正や書き込み失敗時。
    """
    try:
        # rows を一旦 list 化（複数回参照するため）
        rows_list: List[Union[Sequence[Any], Dict[str, Any]]] = list(_iter_rows_copy(rows))

        # 出力パスの準備
        out_path = Path(path).expanduser().resolve()
        if ensure_parent:
            out_path.parent.mkdir(parents=True, exist_ok=True)

        # quoting の解決
        if isinstance(quoting, str):
            q_map = {
                "minimal": csv.QUOTE_MINIMAL,
                "all": csv.QUOTE_ALL,
                "nonnumeric": csv.QUOTE_NONNUMERIC,
                "none": csv.QUOTE_NONE,
            }
            if quoting not in q_map:
                raise CsvWriteError(f"Invalid quoting: {quoting}")
            quoting_val = q_map[quoting]
        else:
            quoting_val = int(quoting)

        # Excel 互換の writer 設定
        # newline="" でオープンし、lineterminator を CRLF に固定
        is_dicts = _is_dict_rows(rows_list)
        with open(out_path, "w", encoding="cp932", errors=encoding_errors, newline="") as f:
            writer = csv.writer(
                f,
                delimiter=delimiter,
                quotechar='"',
                quoting=quoting_val,
                lineterminator="\r\n",
                doublequote=True,
                escapechar=None,
            )

            if is_dicts:
                dict_rows: List[Dict[str, Any]] = rows_list  # type: ignore[assignment]
                cols = _resolve_headers_for_dict_rows(dict_rows, headers)
                if cols:
                    writer.writerow(cols)
                for r in dict_rows:
                    row = [("" if (v is None) else v) for v in (r.get(c) for c in cols)]
                    writer.writerow(row)
            else:
                if headers:
                    writer.writerow(list(headers))
                for r in rows_list:
                    if not isinstance(r, (list, tuple)):
                        raise CsvWriteError("rows must be list[dict] or list[list/tuple]")
                    row = ["" if (v is None) else v for v in r]
                    writer.writerow(row)

        return str(out_path)
    except CsvWriteError:
        raise
    except Exception as e:
        raise CsvWriteError(f"Failed to write CSV: {e}") from e
