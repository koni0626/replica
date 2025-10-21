"""
Language model tool adapters for agent orchestration.

- LangChain の @tool で公開する関数群を定義
- すべての関数は **JSON文字列** を返す（UI／LLMから扱いやすいよう統一）
- 例外は必ず {ok: false, error: "..."} 形式に包んで返す
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, Optional
from pathlib import Path

from langchain_core.tools import tool

# プロジェクト固有の実装（オプショナル）
# services.agents パッケージが存在しない環境でも本モジュールを import 可能にするため、
# ここでは try/except での遅延依存にしておく。
try:
    from services.agents.orchestrator import Orchestrator  # type: ignore
    from services.agents import registry  # type: ignore
    from services.agents.state import Task  # type: ignore
except Exception:
    Orchestrator = None  # type: ignore
    registry = None      # type: ignore
    Task = None          # type: ignore


# ========== ユーティリティ ==========

def _parse_json(s: Optional[str]) -> Dict[str, Any]:
    """与えられたJSON文字列を辞書に変換して返す。失敗時は空dict。
    配列やプリミティブが来た場合は空dictにフォールバックする。
    """
    if not s:
        return {}
    try:
        v = json.loads(s)
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def _to_jsonable(obj: Any) -> Any:
    """dataclass -> dict など、JSONシリアライズ可能な形へ寄せる簡易変換。"""
    try:
        if is_dataclass(obj):
            return asdict(obj)
        if isinstance(obj, dict):
            return {k: _to_jsonable(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [_to_jsonable(v) for v in obj]
        # そのままJSON変換できるタイプは素通し
        json.dumps(obj)
        return obj
    except Exception:
        return str(obj)


def _ok(payload: Dict[str, Any]) -> str:
    """成功レスポンスをJSON文字列で返す。"""
    payload2 = {"ok": True, **payload}
    return json.dumps(payload2, ensure_ascii=False)


def _err(msg: str, **extra: Any) -> str:
    """失敗レスポンスをJSON文字列で返す。"""
    return json.dumps({"ok": False, "error": msg, **extra}, ensure_ascii=False)


def _agents_ready() -> bool:
    return (registry is not None) and (Task is not None)


def _orchestrator_ready() -> bool:
    return (Orchestrator is not None) and _agents_ready()


def _add_to_search_includes(project_id: int, base: Path, abs_file: Path) -> None:
    """search_paths.json の includes へファイルを1件追加する（存在チェック・重複排除）。
    失敗しても例外は外へ投げない（ツール本体のI/Oを阻害しない）。
    """
    try:
        from services.search_path_service import SearchPathService
        rel = abs_file.resolve().relative_to(base).as_posix()
        sps = SearchPathService()
        state = sps.load_state(project_id)
        inc = list(state.get("includes", []) or [])
        exc = list(state.get("excludes", []) or [])
        if rel not in inc:
            inc.append(rel)
        sps.save_state(project_id, inc, exc)
    except Exception:
        # ログに出したい場合はここで print 等に切り替え可能
        pass


# ========== ツール定義 ==========

@tool("agent_run_workflow", return_direct=False)
def agent_run_workflow(project_id: int, goal: str, constraints_json: str = "", run_id: str = "") -> str:
    """直列ワークフローを実行して結果の配列を返す。

    ワークフローの想定: 〔investigator → architect → fixer → reviewer → verifier〕
    必要に応じて Orchestrator 側でスキップ・短絡する場合があります。

    Args:
        project_id: 対象プロジェクトID。
        goal: 実行目的（自然言語でOK）。
        constraints_json: 追加制約の JSON 文字列。例: {"rag_top_k": 8, "patches": [...], "base_ref": "HEAD"}。
        run_id: 実行を識別するID（省略可）。未指定なら自動採番。

    Returns:
        JSON文字列:
        {
          "ok": true,
          "project_id": 123,
          "run_id": "2025-09-14T12-34-56",
          "results": [ {<各ステップの結果>}, ... ],
          "artifacts_dir": "instance/<project_id>/agents/<run_id>",
          "summary": "steps=5, last_intent=verifier"
        }
    """
    try:
        if not _orchestrator_ready():
            return _err("agents_module_not_available", hint="services.agents.* が存在しないためエージェント機能は無効です")
        constraints = _parse_json(constraints_json)
        orch = Orchestrator(project_id=project_id, run_id=run_id or None)  # type: ignore
        results = orch.run_workflow(goal=goal, constraints=constraints)
        payload = {
            "project_id": project_id,
            "run_id": orch.run_id,
            "results": _to_jsonable(results),
            "artifacts_dir": f"instance/{project_id}/agents/{orch.run_id}",
            "summary": f"steps={len(results)}, last_intent={getattr(results[-1].intent, 'name', 'N/A') if results else 'N/A'}",
        }
        return _ok(payload)
    except Exception as e:
        return _err(str(e))


@tool("agent_run_investigator", return_direct=False)
def agent_run_investigator(project_id: int, goal: str, constraints_json: str = "") -> str:
    """調査エージェント（investigator）を1回実行して結果を返す。

    Args:
        project_id: 対象プロジェクトID。
        goal: 調査目的（自然言語）。
        constraints_json: 追加制約の JSON 文字列（任意）。

    Returns:
        {"ok": true, "result": {...}} のJSON文字列。失敗時は {"ok": false, "error": "..."}。
    """
    try:
        if not _agents_ready():
            return _err("agents_module_not_available", hint="services.agents.* が存在しないためエージェント機能は無効です")
        constraints = _parse_json(constraints_json)
        agent = registry.get_agent("investigator")  # type: ignore
        r = agent.run(Task(project_id=project_id, goal=goal, constraints=constraints))  # type: ignore
        return _ok({"result": _to_jsonable(r)})
    except Exception as e:
        return _err(str(e))


@tool("agent_run_architect", return_direct=False)
def agent_run_architect(project_id: int, goal: str, constraints_json: str = "") -> str:
    """設計エージェント（architect）を1回実行して結果を返す。

    Args:
        project_id: 対象プロジェクトID。
        goal: 設計の目標（自然言語）。
        constraints_json: 追加制約の JSON 文字列（任意）。

    Returns:
        {"ok": true, "result": {...}} のJSON文字列。
    """
    try:
        if not _agents_ready():
            return _err("agents_module_not_available", hint="services.agents.* が存在しないためエージェント機能は無効です")
        constraints = _parse_json(constraints_json)
        agent = registry.get_agent("architect")  # type: ignore
        r = agent.run(Task(project_id=project_id, goal=goal, constraints=constraints))  # type: ignore
        return _ok({"result": _to_jsonable(r)})
    except Exception as e:
        return _err(str(e))


@tool("agent_run_fixer", return_direct=False)
def agent_run_fixer(project_id: int, goal: str, constraints_json: str = "") -> str:
    """実装／修正エージェント（fixer）を1回実行して結果を返す。

    Args:
        project_id: 対象プロジェクトID。
        goal: 実装・修正の目標（自然言語）。
        constraints_json: 追加制約の JSON 文字列（任意）。

    Returns:
        {"ok": true, "result": {...}} のJSON文字列。
    """
    try:
        if not _agents_ready():
            return _err("agents_module_not_available", hint="services.agents.* が存在しないためエージェント機能は無効です")
        constraints = _parse_json(constraints_json)
        agent = registry.get_agent("fixer")  # type: ignore
        r = agent.run(Task(project_id=project_id, goal=goal, constraints=constraints))  # type: ignore
        return _ok({"result": _to_jsonable(r)})
    except Exception as e:
        return _err(str(e))


@tool("agent_run_reviewer", return_direct=False)
def agent_run_reviewer(project_id: int, goal: str, constraints_json: str = "") -> str:
    """レビューエージェント（reviewer）を1回実行して結果を返す。

    Args:
        project_id: 対象プロジェクトID。
        goal: レビューの焦点（自然言語）。
        constraints_json: 追加制約の JSON 文字列（任意）。

    Returns:
        {"ok": true, "result": {...}} のJSON文字列。
    """
    try:
        if not _agents_ready():
            return _err("agents_module_not_available", hint="services.agents.* が存在しないためエージェント機能は無効です")
        constraints = _parse_json(constraints_json)
        agent = registry.get_agent("reviewer")  # type: ignore
        r = agent.run(Task(project_id=project_id, goal=goal, constraints=constraints))  # type: ignore
        return _ok({"result": _to_jsonable(r)})
    except Exception as e:
        return _err(str(e))


@tool("agent_run_verifier", return_direct=False)
def agent_run_verifier(project_id: int, goal: str, constraints_json: str = "") -> str:
    """検証エージェント（verifier）を1回実行して結果を返す。

    Args:
        project_id: 対象プロジェクトID。
        goal: 検証の観点（自然言語）。
        constraints_json: 追加制約の JSON 文字列（任意）。

    Returns:
        {"ok": true, "result": {...}} のJSON文字列。
    """
    try:
        if not _agents_ready():
            return _err("agents_module_not_available", hint="services.agents.* が存在しないためエージェント機能は無効です")
        constraints = _parse_json(constraints_json)
        agent = registry.get_agent("verifier")  # type: ignore
        r = agent.run(Task(project_id=project_id, goal=goal, constraints=constraints))  # type: ignore
        return _ok({"result": _to_jsonable(r)})
    except Exception as e:
        return _err(str(e))


@tool("agent_rag_curate", return_direct=False)
def agent_rag_curate(
    project_id: int,
    mode: str = "",
    text: str = "",
    rel_name: str = "",
    paths_json: str = "",
    include_exts: str = "",
    max_chars: int = 1500,
    overlap: int = 200,
    verify_query: str = "",
    verify_top_k: int = 5,
) -> str:
    """RAG取り込みを行うキュレーターエージェント（rag_curator）を実行する。

    使い分け:
        - mode="prompt": text（自由記述）または rel_name（プロジェクト内リソース名）を元にインデックス対象を推定。
        - mode="files" : paths_json（["pathA", "pathB", ...]）を明示指定。include_exts で拡張子フィルタ（"md,txt" など）。
        - mode="build" : 直近の取り込み設定に基づき再構築。include_exts を上書き指定可能。

    Args:
        project_id: 対象プロジェクトID。
        mode: "prompt" | "files" | "build" のいずれか。
        text: プロンプト文字列（prompt モード向け）。
        rel_name: プロジェクト相対の論理名（prompt モード向け）。
        paths_json: 取り込みファイルの配列を表すJSON文字列（files モード向け）。
        include_exts: 対象拡張子をカンマ区切りで指定（例: "md,txt"）。空ならデフォルト。
        max_chars: チャンクの最大長。
        overlap: チャンクのオーバーラップ長。
        verify_query: 取り込み後の簡易検証に使う照会クエリ（空ならスキップ）。
        verify_top_k: 検証時に上位何件を返すか。

    Returns:
        {"ok": true, "result": {...}} のJSON文字列。
    """
    try:
        if not _agents_ready():
            return _err("agents_module_not_available", hint="services.agents.* が存在しないためエージェント機能は無効です")
        constraints: Dict[str, Any] = {
            "mode": mode,
            "text": text,
            "rel_name": rel_name,
            "paths": json.loads(paths_json) if paths_json else None,
            "include_exts": [e.strip() for e in include_exts.split(",") if e.strip()] if include_exts else None,
            "max_chars": max_chars,
            "overlap": overlap,
            "verify_query": verify_query,
            "verify_top_k": verify_top_k,
        }
        agent = registry.get_agent("rag_curator")  # type: ignore
        r = agent.run(Task(project_id=project_id, goal="rag_curate", constraints=constraints))  # type: ignore
        return _ok({"result": _to_jsonable(r)})
    except Exception as e:
        return _err(str(e))


# ========== 追加ツール: CSV 出力（CP932／Windows Excel 互換） ==========

@tool("csv_write_cp932", return_direct=False)
def csv_write_cp932(
    path: str,
    rows_json: str,
    headers_json: str = "",
    delimiter: str = ",",
    quoting: str = "minimal",
    ensure_parent: bool = True,
    encoding_errors: str = "strict",
    project_id: int = 0,
) -> str:
    """CSV を CP932 で書き出す（Windows Excel 互換）。

    doc_path 配下強制:
        project_id を与えた場合、相対パスは doc_path 配下へ解決し、
        絶対パスが doc_path 外を指す場合はエラーを返します（functions.write_file 相当）。

    Args:
        path: 出力先ファイルパス（絶対 or 相対）。
        rows_json: 行データの JSON 配列（list[dict] または list[list/tuple]）。
        headers_json: ヘッダー行の JSON 配列（任意）。dict rows の場合はこれが優先。
        delimiter: 区切り文字（既定 ","）。
        quoting: "minimal"|"all"|"nonnumeric"|"none"。
        ensure_parent: 親ディレクトリを自動作成するか（既定 True）。
        encoding_errors: 文字エラー時の方針（"strict"|"replace"|"ignore"）。
        project_id: 指定時は doc_path 配下強制で書き込み。

    Returns:
        {"ok": true, "path": <書き込み先>, "rows": <件数>, "encoding": "cp932"}
    """
    try:
        from tools.office_csv_tool import write_csv_cp932 as _write_csv_cp932, CsvWriteError
        # rows のパース
        rows = json.loads(rows_json) if rows_json else []
        if not isinstance(rows, list):
            return _err("rows_json must be a JSON array")
        # headers のパース
        headers = None
        if headers_json:
            h = json.loads(headers_json)
            if not isinstance(h, list):
                return _err("headers_json must be a JSON array")
            headers = h

        # パスの正規化（repo/ や ./ を無害化）
        def _norm_user_path(s: str) -> str:
            s2 = str(s or "").replace("\\", "/").strip()
            if not s2:
                return s2
            if s2 == "repo":
                return ""
            if s2.startswith("repo/"):
                return s2[5:]
            if s2.startswith("./"):
                return s2[2:]
            return s2

        target_path = _norm_user_path(path)

        # doc_path 配下強制ロジック
        base: Optional[Path] = None
        if project_id and int(project_id) > 0:
            try:
                from tools.fs_modules import resolve_doc_path
                base = resolve_doc_path(int(project_id))
            except Exception as e:
                return _err("doc_path_resolve_failed", detail=str(e))

            p = Path(target_path).expanduser()
            if not p.is_absolute():
                p = base / p
            p = p.resolve()
            try:
                _ = p.relative_to(base)
            except Exception:
                return _err("path_must_be_under_doc_path", got=str(p), doc_path=str(base))
            abs_out = str(p)
        else:
            # 従来互換（project_id 未指定時は CWD 基準）
            p = Path(target_path).expanduser().resolve()
            abs_out = str(p)

        out = _write_csv_cp932(
            path=abs_out,
            rows=rows,
            headers=headers,
            delimiter=delimiter,
            quoting=quoting,
            ensure_parent=ensure_parent,
            encoding_errors=encoding_errors,
        )

        # search_paths.json の includes に追加（doc_path 指定時のみ）
        try:
            if project_id and int(project_id) > 0 and base is not None:
                _add_to_search_includes(int(project_id), base, Path(abs_out))
        except Exception:
            pass

        return _ok({"path": out, "rows": len(rows), "encoding": "cp932"})
    except CsvWriteError as e:
        return _err(str(e))
    except Exception as e:
        return _err(str(e))


# ========== 追加ツール: CSV → XLSX 変換（出力は doc_path 基準） ==========

@tool("csv_to_xlsx", return_direct=False)
def csv_to_xlsx(
    csv_path: str,
    xlsx_path: str,
    project_id: int,
    encoding: str = "cp932",
    delimiter: str = ",",
    ensure_parent: bool = True,
) -> str:
    """CSV を Excel(.xlsx) に変換して保存する。出力は doc_path 基準。

    仕様:
      - csv_path / xlsx_path に "repo/" や "./" が付いても無害化して doc_path 相対に解決。
      - 絶対パスが与えられた場合でも doc_path 配下でなければ拒否。

    Args:
        csv_path: 入力 CSV のパス（相対は doc_path 基準）。
        xlsx_path: 出力 XLSX のパス（相対は doc_path 基準／doc_path 外は拒否）。
        project_id: 対象プロジェクト。
        encoding: CSV の文字コード（既定 cp932）。
        delimiter: CSV の区切り（既定 ,）。
        ensure_parent: 出力先の親ディレクトリ自動作成。

    Returns:
        {"ok": true, "path": <xlsx絶対パス>, "rows": <書き込み行数>}
    """
    try:
        from tools.office_excel_tool import convert_csv_to_xlsx, ExcelWriteError
        from tools.fs_modules import resolve_doc_path

        def _norm_user_path(s: str) -> str:
            s2 = str(s or "").replace("\\", "/").strip()
            if not s2:
                return s2
            if s2 == "repo":
                return ""
            if s2.startswith("repo/"):
                return s2[5:]
            if s2.startswith("./"):
                return s2[2:]
            return s2

        base = resolve_doc_path(int(project_id))

        # 入力CSV: 相対なら doc_path 基準、絶対なら doc_path 配下であることを要求
        in_rel = _norm_user_path(csv_path)
        inp = Path(in_rel).expanduser()
        if not inp.is_absolute():
            inp = (base / inp).resolve()
        else:
            inp = inp.resolve()
        try:
            _ = inp.relative_to(base)
        except Exception:
            return _err("csv_path_must_be_under_doc_path", got=str(inp), doc_path=str(base))

        # 出力XLSX: 同様の制約
        out_rel = _norm_user_path(xlsx_path)
        outp = Path(out_rel).expanduser()
        if not outp.is_absolute():
            outp = (base / outp).resolve()
        else:
            outp = outp.resolve()
        try:
            _ = outp.relative_to(base)
        except Exception:
            return _err("xlsx_path_must_be_under_doc_path", got=str(outp), doc_path=str(base))

        x_path, rows = convert_csv_to_xlsx(
            csv_path=str(inp),
            xlsx_path=str(outp),
            encoding=encoding,
            delimiter=delimiter,
            ensure_parent=ensure_parent,
        )

        # search_paths.json の includes に追加
        try:
            _add_to_search_includes(int(project_id), base, Path(x_path))
        except Exception:
            pass

        return _ok({"path": x_path, "rows": rows})

    except ExcelWriteError as e:
        return _err(str(e))
    except Exception as e:
        return _err(str(e))


# ========== 新規ツール: Markdown → Word(.docx) 変換（出力は doc_path 基準） ==========

@tool("md_to_docx", return_direct=False)
def md_to_docx(
    md_path: str,
    docx_path: str,
    project_id: int,
    encoding: str = "utf-8",
    ensure_parent: bool = True,
) -> str:
    """Markdown(.md/.markdown) を Word(.docx) に変換して保存する。出力は doc_path 基準。

    仕様:
      - md_path / docx_path に "repo/" や "./" が付いても無害化して doc_path 相対に解決。
      - 絶対パスが与えられた場合でも doc_path 配下でなければ拒否。
      - 変換は pypandoc が利用可能なら優先し、不可なら python-docx の簡易レンダリングにフォールバック。

    Returns:
        {"ok": true, "path": <docx絶対パス>, "paragraphs": <概算段落数>}
    """
    try:
        from tools.fs_modules import resolve_doc_path
        from tools.office_md_tool import convert_md_to_docx, MarkdownToDocxError

        def _norm_user_path(s: str) -> str:
            s2 = str(s or "").replace("\\", "/").strip()
            if not s2:
                return s2
            if s2 == "repo":
                return ""
            if s2.startswith("repo/"):
                return s2[5:]
            if s2.startswith("./"):
                return s2[2:]
            return s2

        base = resolve_doc_path(int(project_id))

        # 入力MD
        in_rel = _norm_user_path(md_path)
        inp = Path(in_rel).expanduser()
        if not inp.is_absolute():
            inp = (base / inp).resolve()
        else:
            inp = inp.resolve()
        try:
            _ = inp.relative_to(base)
        except Exception:
            return _err("md_path_must_be_under_doc_path", got=str(inp), doc_path=str(base))

        # 出力DOCX
        out_rel = _norm_user_path(docx_path)
        outp = Path(out_rel).expanduser()
        if not outp.is_absolute():
            outp = (base / outp).resolve()
        else:
            outp = outp.resolve()
        try:
            _ = outp.relative_to(base)
        except Exception:
            return _err("docx_path_must_be_under_doc_path", got=str(outp), doc_path=str(base))

        out_path, para = convert_md_to_docx(
            md_path=str(inp),
            docx_path=str(outp),
            encoding=encoding,
            ensure_parent=ensure_parent,
        )

        # search_paths.json の includes に追加
        try:
            _add_to_search_includes(int(project_id), base, Path(out_path))
        except Exception:
            pass

        return _ok({"path": out_path, "paragraphs": para})

    except MarkdownToDocxError as e:
        return _err(str(e))
    except Exception as e:
        return _err(str(e))
