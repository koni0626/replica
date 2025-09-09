from typing import Optional, Dict, Any
import json
from urllib.parse import urljoin, urlparse
from typing import List
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


@tool
def fetch_url_links(
    url: str,
    timeout_seconds: int = 15,
    headers_json: str = "",
    allow_redirects: bool = True,
    expected_encoding: Optional[str] = None,
    same_origin_only: bool = False,
    unique: bool = True,
    strip_fragment: bool = True,
    schemes: Optional[List[str]] = None,  # 例: ["http","https"]（Noneなら既定でhttp/httpsのみ）
    max_count: int = 2000,
) -> str:
    """
    指定URLのページ内に含まれるリンク（<a href>）を抽出して返します（JSON）。
    - 相対URLは絶対URLへ解決します（urljoin）。
    - 既定では http/https のみ対象。

    Returns(JSON):
      {
        ok: true/false,
        url: "...",               # 要求URL
        final_url: "...",          # リダイレクト後の最終URL
        status_code: 200,
        encoding: "utf-8",
        link_count: 12,
        links: ["https://example.com/a", ...],
        errors: ["..."]?,
        error?: "..."
      }
    """
    out: Dict[str, Any] = {
        "ok": False,
        "url": url,
        "final_url": None,
        "status_code": None,
        "encoding": None,
        "link_count": 0,
        "links": [],
        "errors": [],
    }
    try:
        import requests
    except Exception as e:
            out["error"] = f"requests_not_available: {type(e).__name__}: {e}"
            return json.dumps(out, ensure_ascii=False)

    # 許可スキーム
    if schemes is None:
        schemes = ["http", "https"]

    try:
        # 追加ヘッダ
        add_headers: Dict[str, str] = {}
        if headers_json:
            try:
                add_headers = json.loads(headers_json) or {}
            except Exception as je:
                out["error"] = f"invalid_headers_json: {type(je).__name__}: {je}"
                return json.dumps(out, ensure_ascii=False)

        headers = {
            "User-Agent": "SystemGen2Bot/1.0 (+https://example.invalid)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        headers.update(add_headers)

        r = requests.get(
            url,
            headers=headers,
            timeout=timeout_seconds,
            allow_redirects=allow_redirects,
        )
        out["status_code"] = int(getattr(r, "status_code", 0))
        out["final_url"] = str(getattr(r, "url", url))

        # 本文取得
        enc = expected_encoding or getattr(r, "encoding", None) or getattr(r, "apparent_encoding", None) or "utf-8"
        out["encoding"] = enc
        try:
            html = r.content.decode(enc, errors="strict")
        except Exception:
            html = r.content.decode(enc, errors="replace")

        base_url = out["final_url"] or url
        base_host = urlparse(base_url).netloc

        links: List[str] = []

        # BeautifulSoupがあれば使う、なければ簡易パーサ
        try:
            from bs4 import BeautifulSoup  # type: ignore
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a.get("href")
                if not href:
                    continue
                abs_url = urljoin(base_url, href)
                pr = urlparse(abs_url)
                if pr.scheme.lower() not in schemes:
                    continue
                if same_origin_only and pr.netloc != base_host:
                    continue
                if strip_fragment and pr.fragment:
                    abs_url = abs_url.split('#', 1)[0]
                links.append(abs_url)
        except Exception as e:
            # フォールバック: 標準ライブラリのHTMLParserで <a href="..."> を拾う
            try:
                from html.parser import HTMLParser
                class LinkParser(HTMLParser):
                    def __init__(self):
                        super().__init__()
                        self.found: List[str] = []
                    def handle_starttag(self, tag, attrs):
                        if tag.lower() != 'a':
                            return
                        href = None
                        for k, v in attrs:
                            if k.lower() == 'href':
                                href = v
                                break
                        if not href:
                            return
                        abs_url = urljoin(base_url, href)
                        pr = urlparse(abs_url)
                        if pr.scheme.lower() not in schemes:
                            return
                        if same_origin_only and pr.netloc != base_host:
                            return
                        if strip_fragment and pr.fragment:
                            abs_url = abs_url.split('#', 1)[0]
                        self.found.append(abs_url)
                lp = LinkParser()
                lp.feed(html)
                links.extend(lp.found)
            except Exception as ie:
                out["errors"].append(f"fallback_parser_failed: {type(ie).__name__}: {ie}")

        # 正規化（unique）
        if unique:
            seen = set()
            uniq = []
            for u in links:
                if u in seen:
                    continue
                seen.add(u)
                uniq.append(u)
            links = uniq

        # 個数制限
        if len(links) > max_count:
            links = links[:max_count]
            out["errors"].append("truncated_links_by_max_count")

        out["links"] = links
        out["link_count"] = len(links)
        out["ok"] = True
        return json.dumps(out, ensure_ascii=False)

    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        return json.dumps(out, ensure_ascii=False)
