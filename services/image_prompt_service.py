from __future__ import annotations
"""
画像生成用のプロンプト整形サービス。

目的:
- ユーザーの短い日本語指示（例:「天才プログラマーっぽい女の子」）から、
  画像生成向けの詳細プロンプト（英語推奨）・ネガティブプロンプト・推奨パラメータを生成する。
- 今後の拡張（Stable Diffusion など）を見据え、negative_prompt や style_tags を返す。

使用例:
    svc = ImagePromptService()
    res = svc.generate(
        user_prompt="天才プログラマーっぽい女の子を描いて",
        preset="jrpg_painterly_anime",
        size="768x768",
        language="ja",
    )
    # res = { ok, prompt, negative_prompt, params:{ size, style_tags }, raw }

実装詳細:
- 既存の GptProvider を利用し、その内部 llm(ChatOpenAI) へ直接メッセージを渡して 1-shot 生成。
- LLM 応答は JSON で返すよう強制。失敗時はヒューリスティックでフォールバック生成。
- ログは抑制（ai_log_enabled=False）。
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import json
import re

from services.gpt_provider import GptProvider


@dataclass
class ImagePromptResult:
    ok: bool
    prompt: str
    negative_prompt: str
    params: Dict[str, Any]
    raw: str


class ImagePromptService:
    def __init__(
        self,
        *,
        model: Optional[str] = None,
        temperature: float = 0.6,
        timeout: int = 60,
        max_retries: int = 2,
    ) -> None:
        self.model = model or "gpt-5"
        self.temperature = temperature
        self.timeout = timeout
        self.max_retries = max_retries

    # --- public API ---
    def generate(
        self,
        *,
        user_prompt: str,
        preset: str = "jrpg_painterly_anime",
        size: str = "768x768",
        language: str = "ja",
    ) -> ImagePromptResult:
        """ユーザー入力から画像生成向けプロンプトを生成する。
        戻り値は JSON 相当の辞書を含む ImagePromptResult。
        """
        up = (user_prompt or "").strip()
        if not up:
            up = "An illustration"

        sys = self._system_prompt_for_preset(preset=preset, size=size, language=language)
        schema = {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "negative_prompt": {"type": "string"},
                "params": {
                    "type": "object",
                    "properties": {
                        "size": {"type": "string"},
                        "style_tags": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["size"],
                },
            },
            "required": ["prompt", "params"],
        }
        # LLM に厳密な JSON を返させるための追加指示
        json_instruction = (
            "You must return ONLY a JSON string that conforms to the given schema. "
            "Do not include explanations or code fences."
        )

        messages = [
            {"role": "system", "content": sys},
            {"role": "system", "content": json_instruction},
            {"role": "user", "content": up},
        ]

        raw = ""
        try:
            provider = GptProvider(
                model=self.model,
                temperature=self.temperature,
                timeout=self.timeout,
                max_retries=self.max_retries,
                ai_log_enabled=False,
            )
            # ChatOpenAI へ直接メッセージを投入
            ai_msg = provider.llm.invoke(messages)
            raw = getattr(ai_msg, "content", "") or ""
            data = self._parse_json_strict(raw)
            if not isinstance(data, dict):
                raise ValueError("invalid_json_type")
            prompt = str(data.get("prompt") or "").strip()
            negative_prompt = str(data.get("negative_prompt") or "").strip()
            params = data.get("params") or {}
            if not isinstance(params, dict):
                params = {}
            # 必須: size
            params.setdefault("size", size)
            # 提案: style_tags
            if not isinstance(params.get("style_tags"), list):
                params["style_tags"] = self._default_style_tags(preset)
            # negative_prompt 既定
            if not negative_prompt:
                negative_prompt = self._default_negative_prompt()

            if not prompt:
                raise ValueError("empty_prompt")

            return ImagePromptResult(
                ok=True,
                prompt=prompt,
                negative_prompt=negative_prompt,
                params=params,
                raw=raw,
            )
        except Exception:
            # フォールバック（ヒューリスティック）
            prompt_fb = self._heuristic_prompt(up, preset=preset)
            return ImagePromptResult(
                ok=True,
                prompt=prompt_fb,
                negative_prompt=self._default_negative_prompt(),
                params={"size": size, "style_tags": self._default_style_tags(preset)},
                raw=raw,
            )

    # --- internals ---
    def _system_prompt_for_preset(self, *, preset: str, size: str, language: str) -> str:
        # 既定プリセット: JRPG 風の厚塗り・ややアニメ寄り
        if preset.lower() in {"jrpg", "jrpg_painterly", "jrpg_painterly_anime", "jrpg_anime"}:
            return (
                "You are an assistant that rewrites a short user's idea into a detailed image-generation prompt.\n"
                "Target style: JRPG-inspired, painterly rendering with slightly anime-like aesthetic.\n"
                "Output language for 'prompt' should be English (better for most image models).\n"
                "Constraints:\n"
                "- Include subject appearance, attire, pose, camera framing (shot type), angle, lighting, mood, background.\n"
                "- Prefer concise comma-separated phrases over long sentences.\n"
                "- Avoid mentioning brand names or copyrighted titles.\n"
                "- Do not include banned content.\n"
                f"- Recommended size: {size}.\n"
                "Return JSON only with keys: prompt, negative_prompt (optional), params.size, params.style_tags.\n"
                "Style hints: anime-inspired, painterly shading, soft lighting, clean lines, vibrant but balanced colors,\n"
                "depth of field, 3/4 angle, medium shot (adjust if more appropriate).\n"
            )
        # デフォルト
        return (
            "You are an assistant that rewrites a short user's idea into a detailed image-generation prompt in English.\n"
            f"Recommended size: {size}. Return JSON only."
        )

    def _default_style_tags(self, preset: str) -> List[str]:
        if preset.lower() in {"jrpg", "jrpg_painterly", "jrpg_painterly_anime", "jrpg_anime"}:
            return [
                "JRPG-inspired",
                "painterly shading",
                "anime-like",
                "soft rim lighting",
                "clean lines",
                "vibrant colors",
                "depth of field",
            ]
        return ["highly detailed", "soft lighting"]

    def _default_negative_prompt(self) -> str:
        return (
            "text, watermark, signature, logo, blurry, low-res, jpeg artifacts, noise, overexposed, underexposed, "
            "extra fingers, deformed hands, malformed anatomy"
        )

    def _parse_json_strict(self, s: str) -> Any:
        # コードフェンスを除去
        t = s.strip()
        if t.startswith("```"):
            t = re.sub(r"^```[a-zA-Z0-9_-]*\n", "", t)
            if t.endswith("```"):
                t = t[:-3]
        # 一度厳密に読む
        try:
            return json.loads(t)
        except Exception:
            # よくあるミス: 末尾カンマ・単一引用符
            t2 = t.replace("'", '"')
            t2 = re.sub(r",\s*([}\]])", r"\1", t2)
            return json.loads(t2)

    def _heuristic_prompt(self, up: str, *, preset: str) -> str:
        base = up.strip()
        if preset.lower() in {"jrpg", "jrpg_painterly", "jrpg_painterly_anime", "jrpg_anime"}:
            return (
                f"An anime-inspired, painterly JRPG-style illustration of {base}, "
                "clean lines, soft rim lighting, vibrant yet balanced colors, medium shot, 3/4 angle, depth of field, "
                "high detail"
            )
        return f"A high-quality illustration of {base}"
