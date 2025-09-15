# tools/agents_tools.py
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

from langchain_core.tools import tool

# プロジェクト固有の実装
from services.agents.orchestrator import Orchestrator
from services.agents import registry
from services.agents.state import Task


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
        constraints = _parse_json(constraints_json)
        orch = Orchestrator(project_id=project_id, run_id=run_id or None)
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
        constraints = _parse_json(constraints_json)
        agent = registry.get_agent("investigator")
        r = agent.run(Task(project_id=project_id, goal=goal, constraints=constraints))
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
        constraints = _parse_json(constraints_json)
        agent = registry.get_agent("architect")
        r = agent.run(Task(project_id=project_id, goal=goal, constraints=constraints))
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
        constraints = _parse_json(constraints_json)
        agent = registry.get_agent("fixer")
        r = agent.run(Task(project_id=project_id, goal=goal, constraints=constraints))
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
        constraints = _parse_json(constraints_json)
        agent = registry.get_agent("reviewer")
        r = agent.run(Task(project_id=project_id, goal=goal, constraints=constraints))
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
        constraints = _parse_json(constraints_json)
        agent = registry.get_agent("verifier")
        r = agent.run(Task(project_id=project_id, goal=goal, constraints=constraints))
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
        agent = registry.get_agent("rag_curator")
        r = agent.run(Task(project_id=project_id, goal="rag_curate", constraints=constraints))
        return _ok({"result": _to_jsonable(r)})
    except Exception as e:
        return _err(str(e))
