import streamlit as st

import crud
from common import get_session

st.title("🗺️ Региональность")
st.caption(
    "Привяжите номер и название региона к подрядчикам и заказчикам. "
    "«✅» перед названием в списке — региональность уже заполнена."
)

session = get_session()

if "_region_catalog" not in st.session_state:
    st.session_state["_region_catalog"] = crud.list_region_catalog(session)

catalog = st.session_state["_region_catalog"]


def _region_widgets(key_prefix):
    """
    Два связанных выпадающих списка (номер / название региона) — выбор в
    одном подставляет пару в другом. Плюс форма добавления новой пары,
    которой ещё нет в справочнике.
    """
    number_options = [""] + [str(r["number"]) for r in catalog]
    name_options = [""] + [r["name"] for r in catalog]

    def _sync_from_number():
        num = st.session_state.get(f"{key_prefix}_num_sel")
        match = next((r for r in catalog if str(r["number"]) == num), None)
        if match:
            st.session_state[f"{key_prefix}_name_sel"] = match["name"]

    def _sync_from_name():
        name = st.session_state.get(f"{key_prefix}_name_sel")
        match = next((r for r in catalog if r["name"] == name), None)
        if match:
            st.session_state[f"{key_prefix}_num_sel"] = str(match["number"])

    col_num, col_name = st.columns(2)
    with col_num:
        st.selectbox(
            "Номер региона",
            options=number_options,
            key=f"{key_prefix}_num_sel",
            on_change=_sync_from_number,
        )
    with col_name:
        st.selectbox(
            "Название региона",
            options=name_options,
            key=f"{key_prefix}_name_sel",
            on_change=_sync_from_name,
        )

    def _add_region():
        # Виджеты "num_sel"/"name_sel" уже отрисованы к этому моменту в
        # текущем прогоне скрипта — Streamlit запрещает менять их
        # session_state напрямую из тела скрипта после отрисовки. Колбэк
        # on_click выполняется ДО повторного прогона скрипта, поэтому здесь
        # это менять можно.
        nm = (st.session_state.get(f"{key_prefix}_new_name") or "").strip()
        num_int = int(st.session_state.get(f"{key_prefix}_new_num") or 0)
        if not nm:
            st.session_state[f"{key_prefix}_add_region_error"] = "Введите название региона."
            return
        if not any(r["number"] == num_int for r in catalog):
            catalog.append({"number": num_int, "name": nm})
        st.session_state[f"{key_prefix}_num_sel"] = str(num_int)
        st.session_state[f"{key_prefix}_name_sel"] = nm
        st.session_state[f"{key_prefix}_add_region_error"] = None
        st.session_state[f"{key_prefix}_new_num"] = 0
        st.session_state[f"{key_prefix}_new_name"] = ""

    with st.popover("➕ Новый регион (нет в списке)"):
        st.number_input(
            "Номер нового региона", min_value=0, step=1, key=f"{key_prefix}_new_num"
        )
        st.text_input("Название нового региона", key=f"{key_prefix}_new_name")
        st.button("Добавить регион", key=f"{key_prefix}_add_region_btn", on_click=_add_region)
        error = st.session_state.get(f"{key_prefix}_add_region_error")
        if error:
            st.warning(error)

    num_val = (st.session_state.get(f"{key_prefix}_num_sel") or "").strip()
    name_val = (st.session_state.get(f"{key_prefix}_name_sel") or "").strip()
    return num_val, name_val


col_left, col_right = st.columns(2)

with col_left:
    st.subheader("Подрядчики")
    suppliers = crud.list_suppliers_with_regions(session)
    if not suppliers:
        st.info("В базе пока нет ни одного поставщика.")
    else:
        options = {s["bin"]: s for s in suppliers}
        selected_bin = st.selectbox(
            "Выберите подрядчика",
            options=list(options.keys()),
            format_func=lambda b: ("✅ " if options[b]["region_number"] else "") + options[b]["name"],
            key="region_supplier_select",
        )
        selected = options[selected_bin]
        if selected["region_number"]:
            st.caption(f"Текущий регион: №{selected['region_number']} — {selected['region_name']}")

        num_val, name_val = _region_widgets("supplier")

        if st.button("💾 Сохранить регион подрядчика", type="primary", key="save_supplier_region_btn"):
            if not num_val or not name_val:
                st.error("Укажите и номер, и название региона.")
            else:
                try:
                    crud.save_supplier_region(
                        session, selected_bin, int(num_val), name_val,
                        username=st.session_state["username"],
                    )
                    st.session_state.pop("_region_catalog", None)
                    st.success("Регион подрядчика сохранён.")
                    st.rerun()
                except Exception as e:
                    session.rollback()
                    st.error(f"Ошибка сохранения: {e}")

with col_right:
    st.subheader("Заказчики")
    customers = crud.list_customers_with_regions(session)
    if not customers:
        st.info("В базе пока нет ни одного заказчика.")
    else:
        options_c = {c["id"]: c for c in customers}
        selected_customer_id = st.selectbox(
            "Выберите заказчика",
            options=list(options_c.keys()),
            format_func=lambda cid: ("✅ " if options_c[cid]["region_number"] else "") + options_c[cid]["name"],
            key="region_customer_select",
        )
        selected_c = options_c[selected_customer_id]
        if selected_c["region_number"]:
            st.caption(f"Текущий регион: №{selected_c['region_number']} — {selected_c['region_name']}")

        num_val_c, name_val_c = _region_widgets("customer")

        if st.button("💾 Сохранить регион заказчика", type="primary", key="save_customer_region_btn"):
            if not num_val_c or not name_val_c:
                st.error("Укажите и номер, и название региона.")
            else:
                try:
                    crud.save_customer_region(
                        session, selected_customer_id, int(num_val_c), name_val_c,
                        username=st.session_state["username"],
                    )
                    st.session_state.pop("_region_catalog", None)
                    st.success("Регион заказчика сохранён.")
                    st.rerun()
                except Exception as e:
                    session.rollback()
                    st.error(f"Ошибка сохранения: {e}")
