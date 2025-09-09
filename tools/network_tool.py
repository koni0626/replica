from typing import Optional, Dict, Any
import json
from langchain_core.tools import tool

# ネットワーク系ツール（HTTPクライアント）
# 依存: requests

@tool
def fetch_url_text(
    url: str,
    timeout_seconds: int = 15,
    headers_json: str = "",
    max_bytes: int = 2_000_000,
    allow_redirects: bool = True,
    expected_encoding: Optional[str] = None,
) -> str:
    """
    指定した URL のテキストを取得して返します（JSON文字列）。

    Args:
      url: 取得先URL（http/https）
      timeout_seconds: タイムアウト秒数（接続/読み取り）
      headers_json: 追加HTTPヘッダのJSON文字列（例: '{"User-Agent":"MyAgent"}')
      max_bytes: 最大読み取りバイト数（超過時は途中まで読み取り、truncated=True を返す）
      allow_redirects: リダイレクトを許可するか
      expected_encoding: サーバ側のcharsetが不正な場合に強制するエンコーディング（例: 'utf-8','cp932'）

    Returns(JSON):
      {
        ok: true/false,
        url: "...",
        final_url: "...",
        status_code: 200,
        encoding: "utf-8",
        truncated: false,
        content: "...",            # 取得テキスト（最大 max_bytes まで）
        headers: { ... },           # レスポンスヘッダ（簡易）
        error?: "...",
      }
    """
    out: Dict[str, Any] = {
        "ok": False,
        "url": url,
        "final_url": None,
        "status_code": None,
        "encoding": None,
        "truncated": False,
        "content": None,
        "headers": {},
    }
    try:
        import requests
    except Exception as e:
        out["error"] = f"requests_not_available: {type(e).__name__}: {e}"
        return json.dumps(out, ensure_ascii=False)

    try:
        # 追加ヘッダ
        add_headers: Dict[str, str] = {}
        if headers_json:
            try:
                add_headers = json.loads(headers_json) or {}
            except Exception as je:
                out["error"] = f"invalid_headers_json: {type(je).__name__}: {je}"
                return json.dumps(out, ensure_ascii=False)

        # 既定の UA を設定（サーバブロック回避用の簡易UA）
        headers = {
            "User-Agent": "SystemGen2Bot/1.0 (+https://example.invalid)",
            "Accept": "*/*",
        }
        headers.update(add_headers)

        # リクエスト（ストリーミングで上限まで読む）
        r = requests.get(
            url,
            headers=headers,
            timeout=timeout_seconds,
            allow_redirects=allow_redirects,
            stream=True,
        )
        out["status_code"] = int(getattr(r, "status_code", 0))
        out["final_url"] = str(getattr(r, "url", url))
        # ヘッダを最低限コピー（巨大化防止）
        try:
            out["headers"] = {k: v for k, v in r.headers.items()}
        except Exception:
            out["headers"] = {}

        # ステータスチェック（ただし 4xx/5xx でも本文を返したいので継続）
        # データ取得
        total = 0
        chunks: list[bytes] = []
        try:
            for chunk in r.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    # 既に超過した分も含めて取り込み、後段でtruncate
                    chunks.append(chunk[: max_bytes - (total - len(chunk))])
                    out["truncated"] = True
                    break
                chunks.append(chunk)
        finally:
            r.close()
        raw = b"".join(chunks)

        # エンコーディング決定
        enc = expected_encoding
        if not enc:
            # requests の推定を優先（charset-normalizer/chardet）
            try:
                enc = r.encoding or r.apparent_encoding
            except Exception:
                enc = None
        if not enc:
            # フォールバック
            enc = "utf-8"
        out["encoding"] = enc

        # デコード（失敗時は cp932 → latin-1 → replace）
        text: str
        try:
            text = raw.decode(enc, errors="strict")
        except Exception:
            try:
                text = raw.decode("cp932", errors="strict")
                out["encoding"] = "cp932"
            except Exception:
                text = raw.decode(enc, errors="replace")

        out["content"] = text
        out["ok"] = True
        return json.dumps(out, ensure_ascii=False)

    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        return json.dumps(out, ensure_ascii=False)
