"""
Общие функции для всех страниц Streamlit-приложения:
- подключение к БД (создаётся один раз за сессию сервера);
- простая авторизация по логину/паролю из .env;
- боковая панель (профиль пользователя, выход) — сама навигация рисуется
  Streamlit автоматически через st.navigation()/st.Page() в app.py.
"""

import hashlib
import os
import secrets
from pathlib import Path

import streamlit as st
from dotenv import dotenv_values
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base

# --- Фирменный стиль BI Group ---------------------------------------------
# Цвета и шрифт подобраны под логотип компании (синее кольцо + "BI GROUP").
BI_BLUE = "#0B5FA6"
BI_BLUE_DARK = "#073E6E"
BI_BLUE_LIGHT = "#EAF3FB"
BI_BLUE_SOFT = "#F5F9FD"
BRAND_FONT = "'Montserrat', 'Segoe UI', Arial, sans-serif"

# Если положить файл логотипа сюда (PNG/SVG, любое имя "logo.*"), он
# автоматически появится в боковой панели и на экране входа — здесь
# используется реальный файл, а не нарисованная копия.
_ASSETS_DIR = Path(__file__).parent / "assets"
_LOGO_PATH = next(
    (p for ext in ("png", "svg", "jpg", "jpeg") for p in _ASSETS_DIR.glob(f"logo.{ext}")),
    None,
)

_env = dotenv_values(".env")

DATABASE_URL = _env.get("DATABASE_URL") or os.getenv("DATABASE_URL")
APP_USERNAME = _env.get("APP_USERNAME") or os.getenv("APP_USERNAME", "admin")
APP_PASSWORD = _env.get("APP_PASSWORD") or os.getenv("APP_PASSWORD", "admin")
AUTH_SECRET = _env.get("AUTH_SECRET") or os.getenv("AUTH_SECRET", secrets.token_hex(32))

_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        if not DATABASE_URL:
            st.error(
                "Не задана переменная окружения DATABASE_URL. "
                "Создайте файл .env на основе .env.example и укажите строку подключения к базе."
            )
            st.stop()
        _engine = create_engine(
            DATABASE_URL,
            pool_pre_ping=True,
            connect_args={"sslmode": "require", "channel_binding": "disable"},
        )
    return _engine


def get_session():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine())
    return _SessionLocal()


@st.cache_resource
def init_database():
    """Создаёт в БД таблицы, которых там ещё нет.

    app.py вызывает эту функцию на каждом переходе между разделами (весь
    скрипт st.navigation перезапускается заново при любом клике по
    навигации) — без кеширования это означало лишний поход в Neon
    (SQLAlchemy сверяет метаданные с реальной схемой перед CREATE TABLE) на
    КАЖДЫЙ переход, заметно тормозя навигацию. @st.cache_resource выполняет
    тело функции только один раз за время жизни процесса сервера — если
    таблицы удалить вручную, потребуется перезапуск приложения, чтобы они
    создались снова (раньше это происходило само собой на следующей
    загрузке PDF; такой автоматический пересоздание — редкий сценарий,
    которым можно пожертвовать ради быстрой навигации на каждый день)."""
    Base.metadata.create_all(get_engine())


def render_logo(width=150):
    """Логотип BI Group — из assets/logo.* (см. _LOGO_PATH), а если файл ещё
    не положен в проект, временная текстовая замена в тех же цветах, чтобы
    страница не оставалась пустой до того, как файл появится."""
    if _LOGO_PATH is not None:
        st.image(str(_LOGO_PATH), width=width)
    else:
        st.markdown(
            f'<div style="font-family:{BRAND_FONT};font-weight:800;font-size:1.3rem;'
            f'color:{BI_BLUE};letter-spacing:0.5px;">BI GROUP</div>',
            unsafe_allow_html=True,
        )


def _make_token(username: str) -> str:
    raw = f"{username}:{AUTH_SECRET}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _check_token(token: str, username: str) -> bool:
    return token == _make_token(username)


def check_auth():
    """Простая проверка логина/пароля. Останавливает выполнение страницы,
    пока пользователь не авторизован."""
    _inject_base_css()

    if not st.session_state.get("authenticated"):
        qp = st.query_params
        token = qp.get("auth_token")
        user = qp.get("auth_user")
        if token and user and _check_token(token, user):
            st.session_state["authenticated"] = True
            st.session_state["username"] = user

    if st.session_state.get("authenticated"):
        # Не .clear() — иначе на каждом прогоне стирались бы любые другие
        # параметры, которые страницы кладут в URL сами (например,
        # выбранный тендер в "Просмотр и правка", чтобы он не терялся при
        # обновлении страницы).
        st.query_params["auth_token"] = _make_token(st.session_state["username"])
        st.query_params["auth_user"] = st.session_state["username"]
        return

    # --- Страница входа ---
    # На этом шаге st.navigation() из app.py ещё не вызван (см. st.stop() ниже),
    # поэтому Streamlit по умолчанию показал бы встроенный список страниц —
    # прячем его отдельным правилом именно здесь.
    _inject_login_css()

    col_left, col_center, col_right = st.columns([1, 1, 1])
    with col_center:
        # st.container(key=...) даёт этому и только этому контейнеру
        # стабильный класс .st-key-login_card — раньше рамка задавалась
        # голым HTML-div, который не оборачивал реальные виджеты формы
        # (каждый st.markdown/st.form — отдельный элемент в DOM) и поэтому
        # рисовался как пустой прямоугольник сам по себе. Обычный
        # :has(>div ...) здесь не годится — под условие "родитель содержит
        # .login-title где-то внутри" попадает сразу вся цепочка предков
        # (колонка, ряд колонок и т.д.), и рамка задваивается/затраивается.
        with st.container(key="login_card"):
            st.markdown(
                '<h2 class="login-title">МЭКС analytics</h2>'
                '<p class="login-subtitle">Система учёта протоколов по конкурсам МЭКС</p>',
                unsafe_allow_html=True,
            )
            with st.form("login_form"):
                username = st.text_input("Логин", key="login_user")
                password = st.text_input("Пароль", type="password", key="login_pass")
                submitted = st.form_submit_button("🔒 Войти", use_container_width=True)

    if submitted:
        if username == APP_USERNAME and password == APP_PASSWORD:
            st.session_state["authenticated"] = True
            st.session_state["username"] = username
            st.query_params.clear()
            st.query_params["auth_token"] = _make_token(username)
            st.query_params["auth_user"] = username
            st.rerun()
        else:
            st.error("❌ Неверный логин или пароль")

    st.stop()


def do_logout():
    """Очищает сессию, query_params и перезагружает страницу."""
    st.session_state.clear()
    st.query_params.clear()
    st.rerun()


def render_sidebar():
    """Профиль пользователя и кнопка выхода в боковой панели — прижаты к
    нижнему краю (см. stSidebarUserContent в CSS ниже). Список страниц
    рисует сам Streamlit (st.navigation в app.py) — здесь никакой навигации
    не дублируем."""
    with st.sidebar:
        st.markdown(
            f'<div class="sidebar-user">👤 <b>{st.session_state.get("username", "")}</b></div>',
            unsafe_allow_html=True,
        )
        st.button("🚪 Выйти", use_container_width=True, on_click=do_logout)


def _inject_base_css():
    """CSS, общий для всех страниц (и авторизованных, и страницы входа) —
    цвета и шрифт подобраны под фирменный стиль BI Group (см. BI_BLUE*)."""
    # Шрифт — отдельным <link>, а не @import внутри <style>: @import
    # блокирует построение CSSOM, пока не подгрузится (или не найдётся в
    # кеше браузера), и делает это на КАЖДЫЙ переход между разделами, т.к.
    # весь блок CSS заново вставляется в DOM при каждом перезапуске
    # скрипта. <link rel="stylesheet"> браузер грузит параллельно и не
    # блокирует отрисовку остального.
    st.markdown(
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@500;600;700;800&display=swap" '
        'rel="stylesheet">',
        unsafe_allow_html=True,
    )
    st.markdown(
        f"""
        <style>
        html, body, [class*="css"] {{
            font-family: {BRAND_FONT};
        }}

        .main .block-container {{
            padding-top: 1.5rem;
        }}

        div.stButton > button {{
            border-radius: 8px;
            font-weight: 500;
            transition: all 0.15s ease;
        }}
        div.stButton > button:hover {{
            transform: translateY(-1px);
            box-shadow: 0 4px 12px rgba(11, 95, 166, 0.18);
        }}
        div.stButton > button[kind="primary"] {{
            background-color: {BI_BLUE};
            border-color: {BI_BLUE};
        }}
        div.stButton > button[kind="primary"]:hover {{
            background-color: {BI_BLUE_DARK};
            border-color: {BI_BLUE_DARK};
        }}

        .stDataFrame {{
            border-radius: 10px;
        }}

        h1, h2, h3 {{
            font-family: {BRAND_FONT};
            font-weight: 700;
            color: {BI_BLUE_DARK};
        }}

        a, a:visited {{
            color: {BI_BLUE};
        }}

        [data-testid="stAppViewContainer"] > .main {{
            background: #fafbfc;
        }}

        section[data-testid="stSidebar"] {{
            background: {BI_BLUE_SOFT};
            border-right: 1px solid #dfe9f5;
        }}

        div[data-baseweb="input"] {{
            border-radius: 8px !important;
        }}
        div[data-testid="stTextInput"] input,
        div[data-testid="stDateInput"] input,
        div[data-testid="stTimeInput"] input {{
            min-height: 42px;
        }}

        [data-testid="stSidebarNav"] li a[aria-current="page"] {{
            background-color: {BI_BLUE_LIGHT} !important;
            color: {BI_BLUE_DARK} !important;
            font-weight: 600 !important;
        }}

        /* Профиль пользователя и кнопка "Выйти" (render_sidebar) — прижаты
        к нижнему краю боковой панели, а не сразу под списком страниц. */
        [data-testid="stSidebarContent"] {{
            display: flex;
            flex-direction: column;
        }}
        [data-testid="stSidebarUserContent"] {{
            margin-top: auto;
        }}

        .sidebar-user {{
            padding: 0.3rem 0.2rem 0.9rem 0.2rem;
            font-size: 0.95rem;
            color: {BI_BLUE_DARK};
            border-bottom: 1px solid #dfe9f5;
            margin-bottom: 0.9rem;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _inject_login_css():
    """Стили и правила, нужные только на странице входа."""
    st.markdown(
        f"""
        <style>
        [data-testid="stSidebarNav"],
        section[data-testid="stSidebar"] {{
            display: none !important;
        }}

        /* .st-key-login_card — стабильный класс контейнера с key="login_card"
        (см. check_auth); в отличие от голого HTML-div, он реально включает
        в себя все вложенные виджеты (заголовок, подпись, форму). */
        .st-key-login_card {{
            background: linear-gradient(135deg, {BI_BLUE_SOFT} 0%, {BI_BLUE_LIGHT} 100%);
            border-radius: 16px;
            padding: 1.8rem 1.5rem;
            margin: 3.5rem auto 0 auto;
            box-shadow: 0 8px 32px rgba(11, 95, 166, 0.15);
            max-width: 380px;
            border-top: 4px solid {BI_BLUE};
        }}
        .login-title, .login-subtitle {{
            text-align: center;
        }}
        .login-title {{
            color: {BI_BLUE_DARK};
            margin-bottom: 0.2rem;
            font-size: 1.35rem;
        }}
        .login-subtitle {{
            color: #6c757d;
            margin-bottom: 1.2rem;
            font-size: 0.85rem;
        }}
        div[data-testid="stFormSubmitButton"] button {{
            background-color: {BI_BLUE};
            color: white;
            border: none;
        }}
        div[data-testid="stFormSubmitButton"] button:hover {{
            background-color: {BI_BLUE_DARK};
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )
