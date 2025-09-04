from flask import Flask, redirect, url_for
from flask_login import current_user, LoginManager
from config import Config
from flask import request, url_for
from extensions import db, csrf, migrate
from controllers import register_blueprints

def create_app():
    app = Flask(__name__, template_folder="templates")
    app.config.from_object(Config)

    db.init_app(app)
    csrf.init_app(app)
    migrate.init_app(app, db)

    # モデル読み込み（重要：自動検出のため）
    from models import load_models
    load_models()

    # Flask-Loginのセットアップ
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'users.login'  # ログインページのエンドポイント

    # ユーザーローダーを設定
    from models.users import Users

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(Users, int(user_id))

    @app.route('/')
    def index():
        if current_user.is_authenticated:
            # ログインしている場合、プロジェクトページにリダイレクト
            return redirect(url_for('projects.index'))
        else:
            # ログインしていない場合、ログインページにリダイレクト
            return redirect(url_for('users.login'))

    register_blueprints(app)
    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", debug=True, use_reloader=False)
