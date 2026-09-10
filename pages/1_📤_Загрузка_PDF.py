import hashlib

import streamlit as st

import crud
from common import get_session
from parser import is_technical_supervision, parse_tender_pdf
from ui_components import render_tender_editor

st.title("📤 Загрузка протокола (PDF)")

# Ограничиваем ширину зоны загрузки
col_upload, col_spacer = st.columns([2, 3])
with col_upload:
    uploaded = st.file_uploader("Выберите PDF-файл протокола результатов конкурса", type=["pdf"])

if uploaded is None:
    st.session_state.pop("_parsed_data", None)
    st.session_state.pop("_last_upload_hash", None)
    st.stop()

file_bytes = uploaded.read()
current_hash = hashlib.sha256(file_bytes).hexdigest()

# Парсим заново только если загружен новый файл (не при каждом перерендере страницы)
if st.session_state.get("_last_upload_hash") != current_hash:
    with st.spinner("Разбираю PDF…"):
        try:
            parsed = parse_tender_pdf(file_bytes)
        except Exception as e:
            st.error(f"Не удалось разобрать файл: {e}")
            st.stop()
    st.session_state["_last_upload_hash"] = current_hash
    st.session_state["_last_upload_name"] = uploaded.name
    st.session_state["_parsed_data"] = parsed
    # сбрасываем состояние виджетов формы от предыдущего файла
    for k in list(st.session_state.keys()):
        if k.startswith("upload_"):
            del st.session_state[k]

st.success(f"Файл распознан: {st.session_state['_last_upload_name']}")
st.info(
    "Проверьте данные ниже. Парсер иногда может ошибиться в названии/адресе, если "
    "они переносятся на несколько строк в самом PDF — поправьте вручную при необходимости. "
    "Пока данные не сохранены в базу, ничего не потеряется."
)

session = get_session()
edited = render_tender_editor(
    st.session_state["_parsed_data"],
    key_prefix="upload",
    category_options=crud.list_lot_categories(session),
)

is_tech_supervision = is_technical_supervision(edited.get("title")) or any(
    is_technical_supervision(lot.get("name")) for lot in edited.get("lots", [])
)
if is_tech_supervision:
    st.warning(
        "⚠️ Похоже, это услуги технического надзора (видно по названию конкурса "
        "или перечню закупаемых работ) — такие конкурсы обычно не сохраняют в базу. "
        "Проверьте перечень работ ниже и, если это действительно технадзор, "
        "просто не нажимайте «Сохранить в базу»."
    )

already_exists = bool(edited["tender_no"]) and crud.tender_exists(session, edited["tender_no"])

overwrite = False
if already_exists:
    st.warning(
        f"Тендер №{edited['tender_no']} уже есть в базе. Сохранение здесь "
        f"полностью заменит его новыми данными из этого файла. Если вы хотите "
        f"просто поправить существующую запись — используйте страницу "
        f"«Просмотр и правка»."
    )
    overwrite = st.checkbox("Да, перезаписать существующую запись этим файлом")

save_clicked = st.button(
    "💾 Сохранить в базу", type="primary", disabled=(already_exists and not overwrite)
)

if save_clicked:
    try:
        tender_no = crud.save_tender(
            session,
            edited,
            source="pdf_upload",
            username=st.session_state["username"],
            source_file=st.session_state["_last_upload_name"],
            file_hash=current_hash,
            overwrite=overwrite,
        )
        st.success(f"Тендер №{tender_no} сохранён в базу данных.")
        for k in ("_last_upload_hash", "_last_upload_name", "_parsed_data"):
            st.session_state.pop(k, None)
        for k in list(st.session_state.keys()):
            if k.startswith("upload_"):
                del st.session_state[k]
    except Exception as e:
        session.rollback()
        st.error(f"Ошибка сохранения: {e}")