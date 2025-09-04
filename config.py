# config.py
import os

class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "change_me")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        "sqlite:///sample.db" # instanceディレクトリの下に作られます
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
