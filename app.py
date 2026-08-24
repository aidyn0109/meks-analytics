import streamlit as st

from common import BI_BLUE, BI_BLUE_DARK, BI_BLUE_LIGHT, check_auth, init_database, render_sidebar

st.set_page_config(page_title="МЭКС analytics", page_icon="📊", layout="wide")

check_auth()
init_database()


def render_home():
    st.markdown(
        """
        <div class="home-hero">
            <h1>МЭКС analytics</h1>
            <p>Система учёта протоколов по конкурсам МЭКС</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        f"""
        <style>
        .home-hero {{ text-align: center; margin-bottom: 2.2rem; }}
        .home-hero h1 {{ margin-bottom: 0.3rem !important; }}
        .home-hero p {{
            color: #6c757d !important;
            font-size: 1.05rem !important;
            font-weight: 400 !important;
            margin: 0 auto !important;
            max-width: 700px;
        }}

        /* Карточка — это st.container(border=True). Растягиваем карточки на
        одинаковую высоту (колонка flex) и прижимаем кнопку "Перейти" к
        нижнему краю, чтобы плитки были одного размера независимо от длины
        описания. */
        div[data-testid="stVerticalBlock"]:has(> div .home-card-body) {{
            display: flex !important;
            flex-direction: column !important;
            min-height: 16rem;
        }}

        .home-card-body {{
            display: flex;
            flex-direction: column;
            align-items: center;
            text-align: center;
        }}

        .home-card-title {{
            width: 100% !important;
            margin: 0 0 0.35rem 0 !important;
            padding: 0 !important;
            font-size: 1.05rem !important;
            font-weight: 600 !important;
            line-height: 1.3 !important;
            color: {BI_BLUE_DARK} !important;
        }}

        /* Streamlit сам добавляет в конец любого markdown-заголовка скрытую
        (видна только при наведении) иконку "ссылка на заголовок" —
        st-testid stHeaderActionElements. Она невидима, но остаётся частью
        строки текста и занимает в ней место; когда заголовок карточки
        переносится на 2 строки, эта иконка "прилипает" к последней строке
        и сама смещает видимый текст этой строки от центра (сам текст
        центрируется вместе с невидимой иконкой, а не отдельно от неё).
        Полностью убираем иконку из потока, а не просто прячем — иначе
        она по-прежнему давила бы на центрирование. */
        .home-card-title [data-testid="stHeaderActionElements"] {{
            display: none !important;
        }}

        .home-card-desc {{
            width: 100% !important;
            margin: 0 !important;
            padding: 0 !important;
            font-size: 0.85rem !important;
            font-weight: 400 !important;
            line-height: 1.45 !important;
            color: #6c757d !important;
        }}

        /* Элемент с кнопкой "Перейти" — прижимаем к низу карточки */
        div[data-testid="stElementContainer"]:has(> .stPageLink) {{
            margin-top: auto;
            padding-top: 0.9rem;
        }}

        /* Кнопка "Перейти" — нативная st.page_link, центрированная и
        оформленная синей рамкой. */
        .stPageLink {{
            display: flex !important;
            justify-content: center !important;
        }}
        .stPageLink [data-testid="stPageLink-NavLink"] {{
            padding: 0.4rem 1.4rem !important;
            font-size: 0.85rem !important;
            font-weight: 600 !important;
            background: transparent !important;
            color: {BI_BLUE} !important;
            border: 2px solid {BI_BLUE} !important;
            border-radius: 8px !important;
            text-align: center !important;
            text-decoration: none !important;
            display: inline-flex !important;
            align-items: center !important;
            white-space: nowrap !important;
            transition: background 0.15s ease, color 0.15s ease;
        }}
        .stPageLink [data-testid="stPageLink-NavLink"]:hover {{
            background: {BI_BLUE} !important;
            color: white !important;
        }}
        .stPageLink [data-testid="stPageLink-NavLink"] p,
        .stPageLink [data-testid="stPageLink-NavLink"] span {{
            color: inherit !important;
            font-weight: 600 !important;
        }}
        /* Внутри кнопки "Перейти" нужен только текст, без значка страницы —
        прячем весь span-обёртку значка (а не только сам эмодзи), иначе
        пустая обёртка всё равно занимает место и текст с стрелкой "→"
        съезжают от центра кнопки вправо. */
        .stPageLink [data-testid="stPageLink-NavLink"] > span:has([data-testid="stIconEmoji"]) {{
            display: none !important;
        }}
        .stPageLink [data-testid="stPageLink-NavLink"] {{
            justify-content: center !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )

    def _icon(path_svg, color):
        # Инлайновый style с !important — самый высокий приоритет в каскаде CSS,
        # выше любого класса Streamlit (в т.ч. с !important на классе/теге),
        # поэтому размер SVG гарантированно одинаковый на всех карточках.
        svg_style = (
            "width:15px !important;height:15px !important;"
            "min-width:15px !important;min-height:15px !important;"
            "display:block !important;flex-shrink:0 !important;"
        )
        return (
            f'<svg viewBox="0 0 24 24" width="15" height="15" style="{svg_style}" '
            f'fill="none" stroke="{color}" stroke-width="2" '
            f'stroke-linecap="round" stroke-linejoin="round">{path_svg}</svg>'
        )

    # Палитра иконок — оттенки синего/холодные тона под цвет логотипа BI
    # Group, вместо разноцветного набора (было: синий/оранжевый/зелёный/
    # фиолетовый), чтобы главная выглядела как единый фирменный стиль.
    ICON_UPLOAD = _icon(
        '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
        '<polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>',
        BI_BLUE,
    )
    ICON_SEARCH = _icon(
        '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
        "#0A4A87",
    )
    ICON_LOG = _icon(
        '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>'
        '<polyline points="14 2 14 8 20 8"/>'
        '<line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>'
        '<line x1="10" y1="9" x2="8" y2="9"/>',
        "#155DA8",
    )
    ICON_REGION = _icon(
        '<path d="M12 21s-7-5.686-7-11a7 7 0 0 1 14 0c0 5.314-7 11-7 11z"/>'
        '<circle cx="12" cy="10" r="2.5"/>',
        "#0E7C86",
    )
    ICON_SCRAPE = _icon(
        '<circle cx="12" cy="12" r="9"/><line x1="3" y1="12" x2="21" y2="12"/>'
        '<path d="M12 3a15.3 15.3 0 0 1 4 9 15.3 15.3 0 0 1-4 9 '
        '15.3 15.3 0 0 1-4-9 15.3 15.3 0 0 1 4-9z"/>',
        "#3949AB",
    )
    ICON_AI = _icon(
        '<path d="M12 3l1.8 4.6L18 9l-4.2 1.4L12 15l-1.8-4.6L6 9l4.2-1.4L12 3z"/>'
        '<path d="M19 15l.7 1.8L21.5 17.5l-1.8.7L19 20l-.7-1.8-1.8-.7 1.8-.7L19 15z"/>',
        "#7C5CFC",
    )

    # "Журнал изменений" — последней в ряду (не по алфавиту/значимости, а
    # по явному пожеланию: остальные разделы — это рабочий процесс с
    # тендером, журнал — вспомогательный, просмотровый).
    cards = [
        (ICON_UPLOAD, BI_BLUE_LIGHT, "Загрузка PDF", page_upload,
         "Загрузите протокол, проверьте и поправьте распознанные данные, сохраните в базу."),
        (ICON_SEARCH, "#E3EEF9", "Просмотр и правка", page_browse,
         "Найдите сохранённый тендер и отредактируйте любые поля."),
        (ICON_REGION, "#E6F5F5", "Региональность", page_regions,
         "Привяжите регион к подрядчикам и заказчикам."),
        (ICON_SCRAPE, "#E8EAF9", "Парсинг данных", page_scraper,
         "Найдите на портале новые завершённые конкурсы и скачайте протоколы итогов."),
        (ICON_AI, "#F0EDFC", "ИИ-ассистент", page_ai,
         "Прогноз баллов подрядчика по будущему конкурсу на основе истории заявок."),
        (ICON_LOG, "#E8F1FB", "Журнал изменений", page_log,
         "История всех изменений, внесённых через приложение."),
    ]

    icon_wrap_style = (
        "width:30px !important;height:30px !important;"
        "min-width:30px !important;min-height:30px !important;"
        "max-width:30px !important;max-height:30px !important;"
        "flex-shrink:0 !important;border-radius:50% !important;"
        "display:flex !important;align-items:center !important;justify-content:center !important;"
        "margin:0 auto 0.6rem auto !important;padding:0 !important;"
    )

    def _render_card(col, icon_svg, bg, title, page_obj, desc):
        with col:
            with st.container(border=True):
                st.markdown(
                    '<div class="home-card-body">'
                    f'<div style="{icon_wrap_style}background:{bg} !important;">{icon_svg}</div>'
                    f'<h3 class="home-card-title" title="{title}">{title}</h3>'
                    f'<p class="home-card-desc">{desc}</p>'
                    '</div>',
                    unsafe_allow_html=True,
                )
                st.page_link(page_obj, label="Перейти")

    cols = st.columns(len(cards))
    for col, card in zip(cols, cards):
        _render_card(col, *card)


page_home = st.Page(render_home, title="Главная", icon="🏠", default=True)
page_upload = st.Page(
    "pages/1_📤_Загрузка_PDF.py", title="Загрузка PDF", icon="📤", url_path="upload"
)
page_browse = st.Page(
    "pages/3_🔍_Просмотр_и_правка.py", title="Просмотр и правка", icon="🔍", url_path="browse"
)
page_log = st.Page(
    "pages/4_📜_Журнал_изменений.py", title="Журнал изменений", icon="📜", url_path="log"
)
page_regions = st.Page(
    "pages/5_🗺️_Региональность.py", title="Региональность", icon="🗺️", url_path="regions"
)
page_scraper = st.Page(
    "pages/6_🌐_Парсинг_данных.py", title="Парсинг данных", icon="🌐", url_path="scraping"
)
page_ai = st.Page(
    "pages/7_🤖_ИИ_ассистент.py", title="ИИ-ассистент", icon="🤖", url_path="ai-assistant"
)

pg = st.navigation(
    {
        "": [page_home, page_upload, page_browse, page_regions, page_scraper, page_ai, page_log],
    }
)

render_sidebar()
pg.run()
