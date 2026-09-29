import hashlib

import pandas as pd
import streamlit as st

import crud
from common import get_session
from parser import parse_tender_pdf
from ui_components import DEFAULT_LOT_CATEGORIES, render_tender_editor

st.title("📤 Загрузка протоколов (PDF)")
st.caption(
    "Можно загрузить сразу пачку протоколов. Приложение разберёт их все, покажет "
    "сводную таблицу для проверки и сохранит в базу только отмеченные — до нажатия "
    "«Сохранить» в базу ничего не попадает."
)

uploaded = st.file_uploader(
    "Выберите PDF-файлы протоколов",
    type=["pdf"],
    accept_multiple_files=True,
)

BULK_KEYS = ("_bulk_key", "_bulk_items")


def _clear_bulk_state():
    for k in BULK_KEYS:
        st.session_state.pop(k, None)
    for k in list(st.session_state.keys()):
        if k.startswith("bulk_"):
            del st.session_state[k]


if not uploaded:
    _clear_bulk_state()
    st.stop()

# Ключ набора файлов — чтобы разбирать заново только при смене самой пачки,
# а не на каждый клик по галочке в таблице. Разбор одного протокола ~0.8 с,
# на 20 файлах это 15 секунд, и повторять их при каждом перерендере нельзя.
batch_key = hashlib.sha256(
    b"".join(hashlib.sha256(f.getvalue()).digest() for f in uploaded)
).hexdigest()

if st.session_state.get("_bulk_key") != batch_key:
    _clear_bulk_state()
    items = []
    progress = st.progress(0.0, text="Разбираю протоколы…")
    for i, f in enumerate(uploaded, start=1):
        file_bytes = f.getvalue()
        item = {
            "file_name": f.name,
            "file_hash": hashlib.sha256(file_bytes).hexdigest(),
            "error": None,
            "data": None,
        }
        try:
            item["data"] = parse_tender_pdf(file_bytes)
        except Exception as e:
            # Один битый файл не должен ронять разбор всей пачки — он
            # поедет дальше с пометкой об ошибке и просто не сохранится.
            item["error"] = str(e)
        items.append(item)
        progress.progress(i / len(uploaded), text=f"Разобрано {i} из {len(uploaded)}…")
    progress.empty()
    st.session_state["_bulk_key"] = batch_key
    st.session_state["_bulk_items"] = items

items = st.session_state["_bulk_items"]
session = get_session()
existing_nos = crud.list_tender_numbers(session)

category_options = [""] + sorted(
    set(DEFAULT_LOT_CATEGORIES) | set(crud.list_lot_categories(session))
)

failed_parse = [it for it in items if it["error"]]
if failed_parse:
    st.error(
        "Не удалось разобрать файлы (они не будут сохранены): "
        + ", ".join(f"{it['file_name']} — {it['error']}" for it in failed_parse)
    )

ok_items = [it for it in items if not it["error"]]
if not ok_items:
    st.stop()


def _lot_category(item):
    """Категория протокола для сводной таблицы — берём у первой позиции
    закупки. У подавляющего большинства протоколов позиция одна; если их
    несколько и категории разные, в таблице показывается первая, а при
    сохранении выбранная в таблице категория проставляется всем позициям
    (для построчной правки есть детальная проверка ниже)."""
    lots = item["data"].get("lots") or []
    return (lots[0].get("category") or "") if lots else ""


def _total_sum(item):
    return sum((lot.get("allocated_amount") or 0) for lot in item["data"].get("lots") or [])


st.subheader(f"Протоколы в пачке: {len(ok_items)}")
st.caption(
    "Категория работ и обе отметки задаются здесь — сразу по всей пачке. "
    "Отметка «Загружать» у конкурса, который уже есть в базе, означает перезапись "
    "его данными из этого файла."
)

grid_df = pd.DataFrame(
    [
        {
            "Файл": it["file_name"],
            "Номер": it["data"].get("tender_no") or "",
            "Название": it["data"].get("title") or "",
            "Заказчик": it["data"].get("customer_name") or "",
            "Сумма, тенге": _total_sum(it) or None,
            "Категория работ": _lot_category(it),
            "Не состоялся": bool(it["data"].get("is_failed")),
            "Технадзор": bool(it["data"].get("is_technical_supervision")),
            "В базе": "уже есть" if (it["data"].get("tender_no") in existing_nos) else "новый",
            "Загружать": (it["data"].get("tender_no") or "") not in existing_nos,
        }
        for it in ok_items
    ]
)

grid = st.data_editor(
    grid_df,
    use_container_width=True,
    hide_index=True,
    key=f"bulk_grid_{batch_key[:8]}",
    column_config={
        "Файл": st.column_config.TextColumn(disabled=True, width="medium"),
        "Номер": st.column_config.TextColumn(disabled=True),
        "Название": st.column_config.TextColumn(disabled=True, width="large"),
        "Заказчик": st.column_config.TextColumn(disabled=True, width="medium"),
        "Сумма, тенге": st.column_config.NumberColumn(disabled=True),
        "Категория работ": st.column_config.SelectboxColumn(options=category_options),
        "Не состоялся": st.column_config.CheckboxColumn(
            help="Проставлено автоматически, если это написано в самом протоколе."
        ),
        "Технадзор": st.column_config.CheckboxColumn(
            help="Проставлено автоматически по названию конкурса и перечню работ."
        ),
        "В базе": st.column_config.TextColumn(disabled=True),
        "Загружать": st.column_config.CheckboxColumn(
            help="Для конкурса, который уже есть в базе, это означает перезапись."
        ),
    },
)

# Правки из таблицы — источник правды для этих четырёх полей (в детальной
# форме ниже они специально скрыты, чтобы не было двух мест для одного
# значения).
for it, (_, row) in zip(ok_items, grid.iterrows()):
    it["data"]["is_failed"] = bool(row["Не состоялся"])
    it["data"]["is_technical_supervision"] = bool(row["Технадзор"])
    it["selected"] = bool(row["Загружать"])
    category = (row["Категория работ"] or "").strip()
    it["category"] = category

st.divider()
st.subheader("Детальная проверка")
st.caption(
    "Открывайте протокол, если нужно поправить распознанные поля: состав комиссии, "
    "заявки, критерии с баллами. Правки сохраняются при переключении между протоколами."
)

labels = {
    i: f"{it['data'].get('tender_no') or '(номер не распознан)'} — {it['file_name']}"
    for i, it in enumerate(ok_items)
}
detail_idx = st.selectbox(
    "Протокол",
    options=list(labels.keys()),
    format_func=lambda i: labels[i],
    key=f"bulk_detail_{batch_key[:8]}",
)

detail_item = ok_items[detail_idx]
edited = render_tender_editor(
    detail_item["data"],
    key_prefix=f"bulk_{detail_idx}",
    category_options=crud.list_lot_categories(session),
    show_flags=False,
)
# Форма не показывает признаки конкурса (ими управляет таблица выше) —
# переносим их из состояния элемента, чтобы не потерять при сохранении.
edited["is_failed"] = detail_item["data"].get("is_failed", False)
edited["is_technical_supervision"] = detail_item["data"].get("is_technical_supervision", False)
detail_item["data"] = edited

st.divider()

to_save = [it for it in ok_items if it.get("selected")]
st.subheader(f"Сохранение: отмечено {len(to_save)} из {len(ok_items)}")

if st.button("💾 Сохранить отмеченные в базу", type="primary", disabled=not to_save):
    saved, errors = [], []
    progress = st.progress(0.0, text="Сохраняю…")
    for i, it in enumerate(to_save, start=1):
        data = dict(it["data"])
        # Категория из сводной таблицы проставляется всем позициям закупки
        # протокола; пустое значение в таблице ничего не затирает.
        if it.get("category"):
            data["lots"] = [dict(lot, category=it["category"]) for lot in data.get("lots", [])]
        try:
            tender_no = crud.save_tender(
                session,
                data,
                source="pdf_upload",
                username=st.session_state["username"],
                source_file=it["file_name"],
                file_hash=it["file_hash"],
                overwrite=True,
            )
            saved.append(tender_no)
        except Exception as e:
            session.rollback()
            errors.append((it["file_name"], str(e)))
        progress.progress(i / len(to_save), text=f"Сохранено {i} из {len(to_save)}…")
    progress.empty()

    if saved:
        st.success(f"Сохранено протоколов: {len(saved)} — {', '.join(saved)}")
    if errors:
        st.error(f"Не удалось сохранить {len(errors)}:")
        st.dataframe(
            pd.DataFrame([{"Файл": f, "Ошибка": e} for f, e in errors]),
            use_container_width=True,
            hide_index=True,
        )
    if saved and not errors:
        st.caption(
            "Можно очистить список файлов выше и загрузить следующую пачку."
        )
