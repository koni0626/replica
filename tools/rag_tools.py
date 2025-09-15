import json
from typing import Optional, List
from langchain_core.tools import tool
from services.rag_service import RagService


@tool
def rag_build_index(project_id: int, include_exts: Optional[List[str]] = None, max_chars: int = 1500, overlap: int = 200, size_limit_bytes: Optional[int] = None) -> str:
    """
    RAG のインデックスを作成（doc_path 配下のテキスト/コードをチャンク化→埋め込み→保存）。
    保存先: instance/<project_id>/index.jsonl
    Args:
        project_id: プロジェクトID
        include_exts: 対象拡張子（例: [".py",".js"]）。未指定時は既定セット
        max_chars: 1チャンクの最大文字数（UTF-8ベース）
        overlap: チャンクの重なり（文字数ベースの近似）
        size_limit_bytes: ファイルサイズ上限（Noneの場合は上限なし）
    Returns:
        JSON文字列 { ok, files, chunks, written, index_path }
    """
    try:
        svc = RagService()
        res = svc.build_index(project_id, include_exts=include_exts, max_chars=max_chars, overlap=overlap, size_limit_bytes=size_limit_bytes)
        return json.dumps(res, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)


@tool
def rag_update_index(project_id: int, paths_json: str, include_exts: Optional[List[str]] = None, max_chars: int = 1500, overlap: int = 200, size_limit_bytes: Optional[int] = None) -> str:
    """
    RAG の部分更新。doc_path 配下の指定パスのみを再インデックスして index.jsonl を更新します。
    Args:
        project_id: プロジェクトID
        paths_json: JSON配列文字列 ["src/app.py","services/"] のように、doc_pathからの相対パス
        include_exts: ディレクトリ指定時の対象拡張子（未指定は既定セット）
        max_chars: チャンク最大文字数
        overlap: チャンクの重なり（文字数ベース）
        size_limit_bytes: ファイルサイズ上限（Noneで上限なし）
    Returns:
        JSON文字列 { ok, targets, kept, chunks, written, index_path }
    """
    try:
        paths = json.loads(paths_json) if paths_json else []
        if not isinstance(paths, list):
            raise ValueError("paths_json must be a JSON array of strings")
        svc = RagService()
        res = svc.update_index(
            project_id,
            paths=paths, include_exts=include_exts, max_chars=max_chars, overlap=overlap, size_limit_bytes=size_limit_bytes
        )
        return json.dumps(res, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
@tool
def rag_index_text(project_id: int, rel_name: str, text: str, max_chars: int = 1500, overlap: int = 200) -> str:
    """
    任意の生テキスト（プロンプト等）をRAGインデックスへ追記します。
    保存先: instance/<project_id>/index.jsonl（file: "prompts/<rel_name>")
    Args:
        project_id: プロジェクトID
        rel_name: 論理的な名前（例: "session-20250914-1200.txt"）
        text: インデックス化するテキスト本文
        max_chars: チャンク最大文字数
        overlap: チャンクの重なり（文字数ベース）
    Returns:
        JSON文字列 { ok, project_id, file, kept, chunks, written, index_path }
    """
    try:
        svc = RagService()
        res = svc.index_plain_text(project_id, rel_name=rel_name, text=text, max_chars=max_chars, overlap=overlap)
        return json.dumps(res, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)


@tool
def rag_query_text(project_id: int, query: str, top_k: int = 8) -> str:
    """
    RAGインデックスからテキストクエリで関連チャンクを検索して返す。
    Returns JSON文字列:
      { hits: [ {file, start, end, text, score}, ... ] }
    """
    try:
        svc = RagService()
        hits = svc.query_text(project_id, query=query, top_k=top_k)
        return json.dumps({"ok": True, "hits": hits}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
