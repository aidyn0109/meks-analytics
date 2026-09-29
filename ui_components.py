"""
Общий компонент редактирования данных тендера — используется на страницах
"Загрузка PDF" и "Просмотр и правка", чтобы не дублировать одну и ту же
форму дважды.
"""

from datetime import date, time, datetime

import pandas as pd
import streamlit as st

DEFAULT_LOT_CATEGORIES = [
    "Водоотведение",
    "Водоснабжение",
    "Теплоснабжение",
    "Электроснабжение",
    "Приборизация/цифровизация",
]


def _as_numeric(df, columns):
    """Приводит числовые колонки к float.

    Колонка, в которой у всех строк пусто, иначе получает тип object, и
    st.data_editor отказывается показывать её как числовую. Пустых колонок
    здесь хватает: состав полей у позиции закупки и у заявки зависит от
    шаблона протокола."""
    for c in columns:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


LOT_COLUMNS = ["name", "category", "enstru_code", "description",
               "quantity", "unit_price", "allocated_amount"]


def _lots_df(lots):
    # Нормализуем: гарантируем наличие всех колонок в каждой строке. Состав
    # полей у позиции закупки зависит от шаблона протокола (код ЕНС ТРУ и
    # описание есть только в новом, количество и цена за единицу — только в
    # старых), поэтому часть колонок у конкретного протокола всегда пустая.
    normalized = [
        {c: lot.get(c, "" if c in ("name", "category", "enstru_code", "description") else None)
         for c in LOT_COLUMNS}
        for lot in (lots or [{}])
    ]
    return _as_numeric(
        pd.DataFrame(normalized)[LOT_COLUMNS],
        ["quantity", "unit_price", "allocated_amount"],
    )


COMMISSION_COLUMNS = ["full_name", "position", "role", "organization",
                      "is_present", "absence_reason"]


def _commission_df(members):
    normalized = [
        {
            "full_name": m.get("full_name", "") or "",
            "position": m.get("position", "") or "",
            "role": m.get("role", "") or "",
            "organization": m.get("organization", "") or "",
            # Признак присутствия трёхзначный: в старых шаблонах его нет
            # вовсе (None), и превращать это в "не присутствовал" нельзя.
            "is_present": m.get("is_present"),
            "absence_reason": m.get("absence_reason", "") or "",
        }
        for m in (members or [{}])
    ]
    df = pd.DataFrame(normalized)[COMMISSION_COLUMNS]
    # Без явного nullable-boolean колонка из одних None получает тип object,
    # и st.data_editor отказывается рисовать её галочками.
    df["is_present"] = df["is_present"].astype("boolean")
    return df


def _bids_df(bids):
    rows = []
    for b in bids or [{}]:
        rows.append(
            {
                "name": b.get("name", "") or "",
                "bin": b.get("bin", "") or "",
                "application_no": b.get("application_no", "") or "",
                "status": b.get("status") or "Допущен",
                "submitted_at": b.get("submitted_at", "") or "",
                "supplier_address": b.get("supplier_address", "") or "",
                "offered_price": b.get("offered_price"),
                "load_factor": b.get("load_factor"),
                "total_score": b.get("total_score"),
                "is_winner": bool(b.get("is_winner")),
                "is_second_place": bool(b.get("is_second_place")),
                "rejection_reason": b.get("rejection_reason") or "",
            }
        )
    return _as_numeric(
        pd.DataFrame(rows), ["offered_price", "load_factor", "total_score"]
    )


def _parse_date_from_widget(val):
    """Преобразует значение из st.date_input в строку ДД.ММ.ГГГГ."""
    if val is None:
        return ""
    if isinstance(val, date):
        return val.strftime("%d.%m.%Y")
    return str(val)


def _parse_time_from_widget(val):
    """Преобразует значение из st.time_input в строку ЧЧ:ММ."""
    if val is None:
        return ""
    if isinstance(val, time):
        return val.strftime("%H:%M")
    return str(val)


def render_tender_editor(data: dict, key_prefix: str, category_options=None,
                          show_flags: bool = True) -> dict:
    """
    Рисует редактируемую форму тендера, инициализированную значениями из
    data (результат parser.parse_tender_pdf, crud.get_tender_dict, либо
    пустой шаблон для ручного ввода). Возвращает текущее состояние формы
    в том же плоском формате — этот dict можно напрямую передать в
    crud.save_tender().

    category_options: категории работ, уже встречавшиеся в базе (см.
    crud.list_lot_categories) — добавляются к стандартному списку
    DEFAULT_LOT_CATEGORIES для выпадающего списка "Категория работ".

    show_flags=False скрывает галочки "конкурс не состоялся" и "технадзор".
    Нужно при массовой загрузке: там этими признаками управляет сводная
    таблица по всей пачке, и вторая пара тех же галочек здесь означала бы
    два источника правды для одного значения. Значения из data при этом
    сохраняются и возвращаются как есть.
    """

    st.subheader("Основные данные тендера")

    # Парсим дату и время из строк для date_input/time_input
    protocol_date_val = None
    raw_date = (data.get("protocol_date") or "").strip()
    if raw_date:
        for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
            try:
                protocol_date_val = datetime.strptime(raw_date, fmt).date()
                break
            except ValueError:
                continue

    protocol_time_val = None
    raw_time = (data.get("protocol_time") or "").strip()
    if raw_time:
        import re
        m = re.search(r"(\d{1,2}):(\d{2})", raw_time)
        if m:
            protocol_time_val = time(int(m.group(1)), int(m.group(2)))

    col1, col2 = st.columns(2)
    with col1:
        tender_no = st.text_input(
            "Номер конкурса", value=data.get("tender_no") or "", key=f"{key_prefix}_tender_no"
        )
        customer_name = st.text_input(
            "Заказчик", value=data.get("customer_name") or "", key=f"{key_prefix}_customer"
        )
        protocol_date = st.date_input(
            "Дата протокола",
            value=protocol_date_val,
            key=f"{key_prefix}_date",
            format="DD.MM.YYYY",
        )
    with col2:
        title = st.text_input(
            "Название конкурса", value=data.get("title") or "", key=f"{key_prefix}_title"
        )
        customer_address = st.text_input(
            "Адрес заказчика", value=data.get("customer_address") or "", key=f"{key_prefix}_address"
        )
        protocol_time = st.time_input(
            "Время протокола",
            value=protocol_time_val,
            key=f"{key_prefix}_time",
            step=60,
        )

    col3, col4, col5 = st.columns(3)
    with col3:
        procurement_method = st.text_input(
            "Способ закупки",
            value=data.get("procurement_method") or "",
            key=f"{key_prefix}_procurement_method",
            help="Есть только в протоколах нового образца («СПОСОБОМ ДВУХЭТАПНЫЙ КОНКУРС»).",
        )
    with col4:
        applications_start_at = st.text_input(
            "Начало приёма заявок (ДД.ММ.ГГГГ ЧЧ:ММ)",
            value=data.get("applications_start_at") or "",
            key=f"{key_prefix}_app_start",
        )
    with col5:
        applications_end_at = st.text_input(
            "Окончание приёма заявок (ДД.ММ.ГГГГ ЧЧ:ММ)",
            value=data.get("applications_end_at") or "",
            key=f"{key_prefix}_app_end",
        )

    is_failed = bool(data.get("is_failed"))
    is_tech_supervision = bool(data.get("is_technical_supervision"))
    if show_flags:
        col_flag1, col_flag2 = st.columns(2)
        with col_flag1:
            is_failed = st.checkbox(
                "Конкурс не состоялся",
                value=is_failed,
                key=f"{key_prefix}_is_failed",
                help=(
                    "Проставляется автоматически, если в протоколе есть фраза "
                    "«Признать закупку … несостоявшейся» — снимите или поставьте "
                    "вручную, если нужно."
                ),
            )
        with col_flag2:
            is_tech_supervision = st.checkbox(
                "Услуги технического надзора",
                value=is_tech_supervision,
                key=f"{key_prefix}_is_tech_supervision",
                help=(
                    "Проставляется автоматически по названию конкурса и перечню "
                    "закупаемых работ. Нужен, чтобы в отчётах отделять надзор от "
                    "самих работ и не задваивать их."
                ),
            )

    st.subheader("Позиции закупки (лоты)")

    # Список категорий работ для выпадающего списка: стандартные + уже
    # встречавшиеся в базе (переданы вызывающей страницей) + уже стоящие в
    # текущих данных (чтобы старые/нестандартные значения не терялись) +
    # добавленные вручную в этой сессии через форму ниже.
    category_state_key = f"{key_prefix}_category_options"
    if category_state_key not in st.session_state:
        merged = list(DEFAULT_LOT_CATEGORIES)
        existing = list(category_options or []) + [
            lot.get("category") for lot in (data.get("lots") or [])
        ]
        for c in existing:
            c = (c or "").strip()
            if c and c not in merged:
                merged.append(c)
        st.session_state[category_state_key] = merged

    with st.popover("➕ Добавить категорию работ"):
        new_category = st.text_input(
            "Название новой категории работ", key=f"{key_prefix}_new_category_input"
        )
        if st.button("Добавить", key=f"{key_prefix}_add_category_btn"):
            nc = new_category.strip()
            if not nc:
                st.warning("Введите название категории.")
            elif nc in st.session_state[category_state_key]:
                st.info("Такая категория уже есть в списке.")
            else:
                st.session_state[category_state_key].append(nc)
                st.success(f"Категория «{nc}» добавлена — теперь доступна в списке ниже.")
                st.rerun()

    lots_result = st.data_editor(
        _lots_df(data.get("lots")),
        num_rows="dynamic",
        use_container_width=True,
        key=f"{key_prefix}_lots",
        column_config={
            "name": "Наименование",
            "category": st.column_config.SelectboxColumn(
                "Категория работ", options=st.session_state[category_state_key]
            ),
            "enstru_code": "Код ЕНС ТРУ",
            "description": "Краткая характеристика работ",
            "quantity": st.column_config.NumberColumn("Количество"),
            "unit_price": st.column_config.NumberColumn("Цена за ед., тенге"),
            "allocated_amount": st.column_config.NumberColumn("Сумма для закупки, тенге"),
        },
    )

    st.subheader("Состав конкурсной комиссии")
    commission_result = st.data_editor(
        _commission_df(data.get("commission_members")),
        num_rows="dynamic",
        use_container_width=True,
        key=f"{key_prefix}_commission",
        column_config={
            "full_name": "Ф.И.О.",
            "position": "Должность",
            "role": "Роль в комиссии",
            "organization": "Организация",
            "is_present": st.column_config.CheckboxColumn("Присутствовал"),
            "absence_reason": "Причина отсутствия",
        },
    )

    st.subheader("Заявки поставщиков")
    st.caption(
        "Отметьте статус, победителя и второе место. Критерии оценки ниже сопоставляются "
        "по БИН, поэтому у заявки с баллами он должен быть заполнен. В протоколах нового "
        "образца БИН печатается только у допущенных поставщиков — у отклонённых его можно "
        "оставить пустым, заявка всё равно сохранится."
    )
    bids_result = st.data_editor(
        _bids_df(data.get("bids")),
        num_rows="dynamic",
        use_container_width=True,
        key=f"{key_prefix}_bids",
        column_config={
            "name": "Наименование поставщика",
            "bin": "БИН",
            "application_no": "Номер заявки",
            "status": st.column_config.SelectboxColumn("Статус", options=["Допущен", "Отклонён"]),
            "submitted_at": "Дата подачи (ДД.ММ.ГГГГ ЧЧ:ММ)",
            "supplier_address": "Почтовый адрес",
            "offered_price": st.column_config.NumberColumn("Цена, тенге (если публикуется)"),
            "load_factor": st.column_config.NumberColumn("Показатель загрузки"),
            "total_score": st.column_config.NumberColumn("Общий балл"),
            "is_winner": st.column_config.CheckboxColumn("Победитель"),
            "is_second_place": st.column_config.CheckboxColumn("2-е место"),
            "rejection_reason": "Причина отклонения",
        },
    )

    st.subheader("Критерии оценки по заявкам")
    st.caption(
        "Выберите БИН поставщика и заполните/поправьте его критерии оценки "
        "(строка = один критерий: название, значение, балл)."
    )

    bin_options = [b for b in bids_result["bin"].tolist() if str(b).strip()]

    criteria_state_key = f"{key_prefix}_criteria_by_bin"
    if criteria_state_key not in st.session_state:
        crit_map = {}
        for b in data.get("bids") or []:
            bin_ = (b.get("bin") or "").strip()
            if bin_:
                crit_rows = b.get("criteria") or [{"name": "", "value": "", "score": None}]
                crit_map[bin_] = _as_numeric(
                    pd.DataFrame(crit_rows)[["name", "value", "score"]], ["score"]
                )
        st.session_state[criteria_state_key] = crit_map

    crit_map = st.session_state[criteria_state_key]

    if bin_options:
        selected_bin = st.selectbox("БИН поставщика", options=bin_options, key=f"{key_prefix}_bin_select")
        current_df = crit_map.get(
            selected_bin, pd.DataFrame([{"name": "", "value": "", "score": None}])
        )
        edited_crit = st.data_editor(
            current_df,
            num_rows="dynamic",
            use_container_width=True,
            key=f"{key_prefix}_crit_{selected_bin}",
            column_config={
                "name": "Критерий",
                "value": "Значение",
                "score": st.column_config.NumberColumn("Балл"),
            },
        )
        crit_map[selected_bin] = edited_crit
        st.session_state[criteria_state_key] = crit_map
    else:
        st.info("Сначала добавьте хотя бы одну заявку с заполненным БИН в таблице выше.")

    result = {
        "tender_no": tender_no.strip(),
        "title": title.strip(),
        "customer_name": customer_name.strip(),
        "customer_address": customer_address.strip(),
        "protocol_date": _parse_date_from_widget(protocol_date),
        "protocol_time": _parse_time_from_widget(protocol_time),
        "procurement_method": procurement_method.strip(),
        "applications_start_at": applications_start_at.strip(),
        "applications_end_at": applications_end_at.strip(),
        "is_failed": bool(is_failed),
        "is_technical_supervision": bool(is_tech_supervision),
        "lots": lots_result.to_dict("records"),
        "commission_members": commission_result.to_dict("records"),
        "bids": [],
    }

    crit_map = st.session_state.get(criteria_state_key, {})
    for row in bids_result.to_dict("records"):
        bin_ = str(row.get("bin") or "").strip()
        criteria_rows = []
        if bin_ and bin_ in crit_map:
            criteria_rows = crit_map[bin_].to_dict("records")
        result["bids"].append({**row, "bin": bin_, "criteria": criteria_rows})

    return result