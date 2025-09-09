# config.py
import os

class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "change_me")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        "sqlite:///replica.db" # instanceディレクトリの下に作られます
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # ============== LLMモデル選択関連（UIから指定可能） ==============
    # 既定モデル
    DEFAULT_LLM_MODEL = os.environ.get("DEFAULT_LLM_MODEL", "gpt-5")
    # 許可モデル（ホワイトリスト）
    ALLOWED_LLM_MODELS = os.environ.get("ALLOWED_LLM_MODELS", "gpt-5,gpt-4o").split(',')
