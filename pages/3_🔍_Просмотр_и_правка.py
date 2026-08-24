import streamlit as st

import crud
from common import get_session
from ui_components import render_tender_editor

st.title("🔍 Просмотр и правка сохранённых протоколов")

session = get_session()
tenders = crud.list_tenders(session)

if not tenders:
    st.info("В базе пока нет ни одного тендера. Загрузите PDF или введите протокол вручную.")
    st.stop()

options = {f"{t.tender_no} — {(t.title or '')[:70]}": t.tender_no for t in tenders}
option_labels = list(options.keys())

# Запоминаем выбранный тендер в URL (?tender=...). Без этого при обновлении
# страницы (F5) session_state теряется, список открывается на первом
# тендере из сортировки — не на том, что редактировали, и кажется, что
# сохранённые изменения пропали (на самом деле просто открылся другой тендер).
default_index = 0
requested_tender = st.query_params.get("tender")
if requested_tender:
    for i, t in enumerate(tenders):
        if t.tender_no == requested_tender:
            default_index = i
            break

label = st.selectbox("Выберите тендер", options=option_labels, index=default_index)
tender_no = options[label]
st.query_params["tender"] = tender_no

# Свой набор ключей виджетов на каждый тендер (edit_<номер>_...). Без этого
# при переключении тендера в списке st.text_input/date_input/time_input не
# обновляют отображаемое значение: Streamlit хранит их состояние на
# фронтенде по ключу виджета и не подхватывает новый value, даже если сам
# ключ был удалён и пересоздан в том же прогоне скрипта (в отличие от
# st.data_editor, который значение обновляет корректно, — из-за этого
# лоты обновлялись при смене тендера, а поля вверху формы — нет).
key_prefix = f"edit_{tender_no}"

# Подгружаем данные заново только при смене выбранного тендера
if st.session_state.get("_edit_tender_no") != tender_no:
    st.session_state["_edit_tender_no"] = tender_no
    data = crud.get_tender_dict(session, tender_no)
    if data is None:
        st.error(f"Тендер №{tender_no} не найден в базе данных.")
        st.stop()
    st.session_state["_edit_data"] = data
    # Чистим ключи предыдущего тендера, чтобы session_state не рос бесконечно
    for k in list(st.session_state.keys()):
        if k.startswith("edit_") and not k.startswith(key_prefix):
            del st.session_state[k]

# Страховка: если _edit_data отсутствует (после сброса сессии), загружаем заново
if "_edit_data" not in st.session_state:
    data = crud.get_tender_dict(session, tender_no)
    if data is None:
        st.error(f"Тендер №{tender_no} не найден в базе данных.")
        st.stop()
    st.session_state["_edit_data"] = data

st.divider()
edited = render_tender_editor(
    st.session_state["_edit_data"],
    key_prefix=key_prefix,
    category_options=crud.list_lot_categories(session),
)

col_save, col_delete = st.columns([3, 1])
with col_save:
    if st.button("💾 Сохранить изменения", type="primary", use_container_width=True):
        try:
            new_tender_no = crud.save_tender(
                session,
                edited,
                source="manual_edit",
                username=st.session_state["username"],
                overwrite=True,
                old_tender_no=tender_no,
            )
            st.success("✅ Изменения сохранены.")
            if new_tender_no != tender_no:
                # Номер конкурса переименован — переключаем сессию на новый
                # номер, иначе список тендеров и открытая форма разъедутся
                # (старого номера в базе больше нет).
                for k in list(st.session_state.keys()):
                    if k.startswith(key_prefix):
                        del st.session_state[k]
                st.session_state["_edit_tender_no"] = new_tender_no
                st.query_params["tender"] = new_tender_no
            st.session_state["_edit_data"] = crud.get_tender_dict(session, new_tender_no)
            st.rerun()
        except Exception as e:
            session.rollback()
            st.error(f"Ошибка сохранения: {e}")
with col_delete:
    with st.popover("🗑️ Удалить протокол", use_container_width=True):
        st.warning(f"⚠️ Вы собираетесь полностью удалить протокол **№{tender_no}**.")
        st.markdown("Это действие **необратимо**: будут удалены все лоты, заявки, критерии и комиссия.")
        confirm_text = st.text_input(
            "Введите номер конкурса для подтверждения:",
            key=f"{key_prefix}_delete_confirm",
            placeholder=tender_no,
        )
        if st.button(
            "🗑️ Да, удалить безвозвратно",
            type="secondary",
            disabled=(confirm_text.strip() != tender_no),
            use_container_width=True,
        ):
            try:
                crud.delete_tender(session, tender_no, username=st.session_state["username"])
                st.success(f"Протокол №{tender_no} полностью удалён из базы данных.")
                st.query_params.pop("tender", None)
                st.session_state.pop("_edit_data", None)
                st.session_state.pop("_edit_tender_no", None)
                for k in list(st.session_state.keys()):
                    if k.startswith("edit_"):
                        del st.session_state[k]
                st.rerun()
            except Exception as e:
                session.rollback()
                st.error(f"Ошибка удаления: {e}")