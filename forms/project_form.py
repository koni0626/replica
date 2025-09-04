# forms/user_form.py
from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField
from wtforms.validators import DataRequired, Length

class ProjectRegisterForm(FlaskForm):
    project_name = StringField("プロジェクト名", validators=[DataRequired(), Length(min=1, max=32)])
    description = StringField("説明", validators=[DataRequired(), Length(max=1024)])
    # create時は手入力、edit時はreadonly表示。バリデーションはコントローラ側でZIPと合わせて扱う
    doc_path = StringField("ドキュメントのパス", validators=[Length(max=260)])
    submit = SubmitField("登録")
