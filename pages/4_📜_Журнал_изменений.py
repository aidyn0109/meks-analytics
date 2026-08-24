import pandas as pd
import streamlit as st
from sqlalchemy import select

from common import get_session
from models import AuditLog

st.title("📜 Журнал изменений")
st.caption("Все действия, выполненные через это приложение: загрузка PDF, ручной ввод, ручная правка.")

session = get_session()
limit = st.slider("Сколько последних записей показать", 10, 500, 100)

logs = session.execute(
    select(AuditLog).order_by(AuditLog.changed_at.desc()).limit(limit)
).scalars().all()

if not logs:
    st.info("Изменений пока не зафиксировано.")
    st.stop()

SOURCE_LABELS = {
    "pdf_upload": "Загрузка PDF",
    "manual_entry": "Ручной ввод",
    "manual_edit": "Ручная правка",
    "manual_delete": "Удаление",
}
ACTION_LABELS = {"INSERT": "Создание", "UPDATE": "Изменение", "DELETE": "Удаление"}

df = pd.DataFrame(
    [
        {
            "Дата/время": l.changed_at,
            "Тендер": l.record_id,
            "Действие": ACTION_LABELS.get(l.action, l.action),
            "Источник": SOURCE_LABELS.get(l.source, l.source),
            "Пользователь": l.changed_by,
            "_id": l.id,
        }
        for l in logs
    ]
)
st.dataframe(df.drop(columns=["_id"]), use_container_width=True)

st.subheader("Детали записи")
selected_id = st.selectbox(
    "Выберите запись для просмотра деталей (что именно поменялось)",
    options=df["_id"].tolist(),
    format_func=lambda i: f"#{i} — " + df.loc[df['_id'] == i, 'Тендер'].values[0],
)
log = next(l for l in logs if l.id == selected_id)

col1, col2 = st.columns(2)
with col1:
    st.markdown("**Было:**")
    st.json(log.old_data or {})
with col2:
    st.markdown("**Стало:**")
    st.json(log.new_data or {})
