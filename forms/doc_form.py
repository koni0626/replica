# forms/docs_form.py
from flask_wtf import FlaskForm
from wtforms import TextAreaField, HiddenField, SubmitField
from wtforms.validators import DataRequired

class DocForm(FlaskForm):
    prompt = TextAreaField("プロンプト", validators=[DataRequired()])
    generated_content = TextAreaField("生成結果（プレビュー）")  # 画面に表示用（読み取り専用扱い）
    # 隠しフィールドに保持してもOK（ここではvisibleのTextAreaにして編集もできるように）
    submit_generate_tool = SubmitField("生成")
    submit_commit   = SubmitField("保存")
