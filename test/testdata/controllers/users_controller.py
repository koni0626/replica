from flask import Blueprint, render_template, redirect, url_for, flash
from flask_login import login_user, logout_user, current_user
from services.user_service import UserService
from forms.user_form import RegistrationForm, LoginForm


user_bp = Blueprint('users', __name__)

@user_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('projects.index'))
    form = RegistrationForm()
    if form.validate_on_submit():
        UserService.add_user(form.username.data, form.email.data, form.password.data)
        flash('ユーザーが登録されました。')
        return redirect(url_for('users.login'))
    return render_template('users/register.html', form=form)

@user_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('projects.index'))
    form = LoginForm()
    if form.validate_on_submit():
        user = UserService.get_user_by_username(form.username.data)
        if user is None or not user.check_password(form.password.data):
            flash('無効なユーザー名またはパスワードです。')
            return redirect(url_for('users.login'))
        login_user(user)
        return redirect(url_for('projects.index'))
    return render_template('users/login.html', form=form)

@user_bp.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('projects.index'))

@user_bp.route('/users')
def user_list():
    users = UserService.get_all_users()
    return render_template('users/list.html', users=users)

@user_bp.route('/delete_user/<int:user_id>')
def delete_user(user_id):
    UserService.delete_user(user_id)
    flash('ユーザーが削除されました。')
    return redirect(url_for('user.user_list'))