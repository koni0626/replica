from __future__ import annotations
import base64
import logging
import os
import re
from typing import Tuple


logger = logging.getLogger(__name__)


class ImageService:
    """
    画像生成サービス（簡易）。
    - OpenAI の Images API が利用可能なら PNG を生成
    - それ以外の環境では SVG フォールバックを返す

    generate(prompt, size) -> (bytes, mime, ext)

    ログ強化ポイント:
    - generate 呼び出し時にサイズとプロンプト先頭(最大120文字)を DEBUG で記録
    - OpenAI での生成成功時は INFO で出力
    - OpenAI 生成失敗時は例外スタックを含めて ERROR（exception）で出力
    - フォールバック使用時は WARNING で出力
    - OPENAI_API_KEY 未設定や SDK 未導入のときは DEBUG で通知
    """

    def __init__(self) -> None:
        self._openai_key = os.getenv("OPENAI_API_KEY") or ""

    # ---- public API -----------------------------------------------------
    def generate(self, prompt: str, size: str = "512x512") -> Tuple[bytes, str, str]:
        raw_prompt = (prompt or "")
        norm_prompt = raw_prompt.strip() or "An illustration"
        size = (size or "512x512").strip() or "512x512"

        # 入力ログ（プロンプト全文は残さず、先頭だけ短縮）
        logger.debug(
            "ImageService.generate called (size=%s, prompt[0:120]=%r)",
            size,
            norm_prompt[:120],
        )

        # まずは OpenAI 経由を試み、失敗時は SVG フォールバック
        if self._can_use_openai():
            try:
                # OpenAIの許容サイズに正規化
                openai_size = self._normalize_openai_size(size)
                if openai_size != size:
                    logger.debug("ImageService: normalize size %s -> %s for OpenAI", size, openai_size)
                bytes_, mime, ext = self._generate_via_openai(norm_prompt, openai_size)
                logger.info(
                    "ImageService: generated via OpenAI (size=%s, bytes=%d)",
                    openai_size,
                    len(bytes_) if bytes_ else -1,
                )
                return bytes_, mime, ext
            except Exception as e:
                # スタックトレース込みで出力
                logger.exception(
                    "ImageService: OpenAI generation failed (size=%s): %s",
                    size,
                    e,
                )
        else:
            logger.debug("ImageService: OPENAI_API_KEY not set or SDK unavailable. Fallback to SVG.")

        # フォールバック（SVG）
        bytes_, mime, ext = self._generate_svg_fallback(norm_prompt, size)
        logger.warning(
            "ImageService: using SVG fallback (size=%s, bytes=%d)",
            size,
            len(bytes_) if bytes_ else -1,
        )
        return bytes_, mime, ext

    # ---- internal helpers ----------------------------------------------
    def _can_use_openai(self) -> bool:
        if not self._openai_key:
            return False
        try:
            import openai  # type: ignore  # noqa: F401
            return True
        except Exception:
            return False

    def _normalize_openai_size(self, size: str) -> str:
        """
        OpenAI Images API (gpt-image-1 など) が許容するサイズへ正規化する。
        許容: "1024x1024", "1024x1536"(縦), "1536x1024"(横), "auto"
        その他の指定（"512x512", "768x768" など）はアスペクト比で最も近い許容値に丸める。
        """
        allowed = {"1024x1024", "1024x1536", "1536x1024", "auto"}
        s = (size or "").strip().lower()
        if s in allowed:
            return s
        # 数値の WxH ならアスペクト比で丸める
        m = re.match(r"^(\d+)x(\d+)$", s)
        if m:
            w, h = int(m.group(1)), int(m.group(2))
            if w <= 0 or h <= 0:
                return "1024x1024"
            if w == h:
                return "1024x1024"
            return "1536x1024" if w > h else "1024x1536"
        # 不明指定は auto にする
        return "auto"

    def _generate_via_openai(self, prompt: str, size: str) -> Tuple[bytes, str, str]:
        import openai  # type: ignore
        client_cls = getattr(openai, "OpenAI", None)
        png_bytes: bytes | None = None
        if client_cls:
            # 新SDK (openai>=1.x) の images.generate は response_format パラメータを受け付けません。
            # デフォルトで b64_json が返る実装に合わせ、明示指定は行わないように修正。
            cli = client_cls(api_key=self._openai_key)
            resp = cli.images.generate(
                model="gpt-image-1",
                prompt=prompt,
                size=size or "auto",
            )
            # data[0].b64_json が基本。万一無い場合は url を確認（取得不可なら例外にしてフォールバックへ）。
            data0 = getattr(resp, "data", [None])[0]
            b64 = None
            url = None
            if isinstance(data0, dict):
                b64 = data0.get("b64_json")
                url = data0.get("url")
            else:
                b64 = getattr(data0, "b64_json", None)
                url = getattr(data0, "url", None)
            if b64:
                png_bytes = base64.b64decode(b64)
            else:
                raise RuntimeError("OpenAI Images API returned no b64_json (url only).")
        else:
            # 旧SDK互換（openai<1.0）。こちらは response_format が必要。
            openai.api_key = self._openai_key
            resp = openai.Image.create(
                prompt=prompt,
                size=size or "1024x1024",
                response_format="b64_json",
            )
            b64 = resp["data"][0]["b64_json"]
            png_bytes = base64.b64decode(b64)
        return (png_bytes, "image/png", "png")

    def _generate_svg_fallback(self, prompt: str, size: str) -> Tuple[bytes, str, str]:
        w, h = self._parse_size(size)
        safe = (prompt[:200]).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        hue = (sum(ord(c) for c in safe) + w + h) % 360
        svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="hsl({hue},70%,85%)"/>
      <stop offset="100%" stop-color="hsl({(hue+45)%360},70%,70%)"/>
    </linearGradient>
  </defs>
  <rect x="0" y="0" width="{w}" height="{h}" fill="url(#g)"/>
  <rect x="8" y="8" width="{w-16}" height="{h-16}" fill="rgba(255,255,255,0.6)" rx="12"/>
  <text x="50%" y="50%" dominant-baseline="middle" text-anchor="middle"
        font-size="{max(12, int(min(w, h) * 0.06))}" font-family="Arial, Helvetica, sans-serif"
        fill="#333" style="white-space: pre-wrap">{safe}</text>
</svg>'''.encode("utf-8")
        return (svg, "image/svg+xml", "svg")

    def _parse_size(self, size: str) -> Tuple[int, int]:
        # SVGフォールバック用に数値へ解釈。"auto" や不正値は 1024x1024 にする。
        s = (size or "").strip().lower()
        if s == "auto":
            return (1024, 1024)
        m = re.match(r"^(\d+)\s*x\s*(\d+)$", s)
        if not m:
            return (1024, 1024)
        w, h = int(m.group(1)), int(m.group(2))
        w = max(64, min(w, 2048))
        h = max(64, min(h, 2048))
        return (w, h)
