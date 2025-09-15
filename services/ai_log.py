from __future__ import annotations
import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


class AiRunLogger:
    """
    セッション（1回のLLM実行）単位で、テキスト(.log)とJSONL(.jsonl)へログを書き出す簡易ロガー。
    - 保存先: instance/<project_id>/ai_logs/<run_id>.log, .jsonl
    - 使い方:
        logger = AiRunLogger(project_id)
        logger.start_session({...})
        logger.messages_initial([...])
        logger.turn_start(1, conversation_len=5)
        logger.ai_raw(1, content="...", tool_calls_preview=[...])
        logger.tool_call(1, name, args, call_id)
        logger.tool_result(1, call_id, result)
        logger.final_text(text)
        logger.end_session(status="ok")
        logger.close()
    """

    def __init__(self, project_id: int, run_id: Optional[str] = None, base_dir: Optional[str] = None, enabled: bool = True):
        self.enabled = enabled
        self.project_id = project_id
        self.run_id = run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
        self.base_dir = base_dir or f"instance/{project_id}/ai_logs"
        self._lock = threading.Lock()
        self._fp_text = None
        self._fp_json = None
        if self.enabled:
            Path(self.base_dir).mkdir(parents=True, exist_ok=True)
            self._fp_text = open(os.path.join(self.base_dir, f"{self.run_id}.log"), "a", encoding="utf-8")
            self._fp_json = open(os.path.join(self.base_dir, f"{self.run_id}.jsonl"), "a", encoding="utf-8")

    # ---- 基本I/O ----
    def _ts(self) -> str:
        return datetime.now().strftime("%Y/%m/%d %H:%M:%S")

    def _iso(self) -> str:
        return datetime.now().isoformat(timespec="seconds")

    def _redact(self, obj: Any) -> Any:
        """簡易レダクション: 機微なキーや長すぎるトークンらしき値をマスク。"""
        SENSITIVE_KEYS = {"api_key", "apikey", "authorization", "Authorization", "token", "access_token", "password", "secret"}
        try:
            if isinstance(obj, dict):
                out: Dict[str, Any] = {}
                for k, v in obj.items():
                    if k in SENSITIVE_KEYS:
                        out[k] = "*****"
                    else:
                        out[k] = self._redact(v)
                return out
            elif isinstance(obj, list):
                return [self._redact(v) for v in obj]
            elif isinstance(obj, str):
                if len(obj) > 4000:
                    return obj[:4000] + "...(truncated)"
                return obj
            else:
                return obj
        except Exception:
            return obj

    def _write_text(self, line: str) -> None:
        if not self.enabled or not self._fp_text:
            return
        with self._lock:
            self._fp_text.write(line + "\n")
            self._fp_text.flush()

    def _write_jsonl(self, event_type: str, payload: Dict[str, Any]) -> None:
        if not self.enabled or not self._fp_json:
            return
        with self._lock:
            safe = self._redact(payload)
            rec = {"ts": self._iso(), "type": event_type, **safe}
            self._fp_json.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self._fp_json.flush()

    # ---- API ----
    def start_session(self, meta: Dict[str, Any]) -> None:
        self._write_text(f"[{self._ts()}] [SESSION-START] project={self.project_id} run_id={self.run_id}")
        self._write_jsonl("session_start", {"project_id": self.project_id, "run_id": self.run_id, "meta": meta})

    def end_session(self, status: str = "ok", summary: Optional[str] = None) -> None:
        self._write_text(f"[{self._ts()}] [SESSION-END] status={status} summary={summary or ''}")
        self._write_jsonl("session_end", {"status": status, "summary": summary})

    def info(self, msg: str, extra: Optional[Dict[str, Any]] = None) -> None:
        self._write_text(f"[{self._ts()}] [INFO] {msg}")
        if extra:
            self._write_jsonl("info", {"msg": msg, "extra": extra})

    def error(self, msg: str, exc: Optional[BaseException] = None, extra: Optional[Dict[str, Any]] = None) -> None:
        self._write_text(f"[{self._ts()}] [ERROR] {msg} {exc or ''}")
        self._write_jsonl("error", {"msg": msg, "exception": str(exc) if exc else None, "extra": extra or {}})

    def messages_initial(self, messages: Any) -> None:
        self._write_jsonl("messages_initial", {"count": len(messages) if hasattr(messages, '__len__') else None, "messages": messages})

    def turn_start(self, turn: int, conversation_len: int) -> None:
        self._write_text(f"[{self._ts()}] [TURN {turn} START] conversation_len={conversation_len}")
        self._write_jsonl("turn_start", {"turn": turn, "conversation_len": conversation_len})

    def ai_raw(self, turn: int, content: Any, tool_calls_preview: Optional[Any] = None) -> None:
        self._write_jsonl("ai_raw", {"turn": turn, "content": content, "tool_calls": tool_calls_preview or []})

    def tool_call(self, turn: int, name: str, args: Dict[str, Any], call_id: Optional[str] = None) -> None:
        self._write_text(f"[{self._ts()}] [TOOL CALL] {name}")
        self._write_jsonl("tool_call", {"turn": turn, "name": name, "args": args, "call_id": call_id})

    def tool_result(self, turn: int, call_id: Optional[str], result: Any) -> None:
        self._write_jsonl("tool_result", {"turn": turn, "call_id": call_id, "result": result})

    def final_text(self, text: Any) -> None:
        self._write_jsonl("final_text", {"text": text})

    def close(self) -> None:
        try:
            if self._fp_text:
                self._fp_text.close()
            if self._fp_json:
                self._fp_json.close()
        except Exception:
            pass
