import streamlit as st

import ai_assistant
import crud
from common import get_session

st.title("🤖 ИИ-ассистент")
st.caption(
    "Прогноз баллов подрядчика по гипотетическому будущему конкурсу — на основе "
    "истории его реальных заявок и подрядчиков того же региона, уже известных базе."
)

session = get_session()

suppliers = crud.list_suppliers_with_regions(session)
regions = crud.list_region_catalog(session)

if not suppliers:
    st.info("В базе пока нет ни одного поставщика.")
    st.stop()
if not regions:
    st.info("В базе пока не задан ни один регион — сначала заполните вкладку «Региональность».")
    st.stop()

supplier_options = {s["bin"]: s for s in suppliers}
selected_bin = st.selectbox(
    "Подрядчик (можно начать вводить название, чтобы найти нужного в списке)",
    options=list(supplier_options.keys()),
    format_func=lambda b: supplier_options[b]["name"],
)

col2, col3 = st.columns([2, 1])
with col2:
    region_options = {r["number"]: r["name"] for r in regions}
    selected_region = st.selectbox(
        "Регион будущего конкурса",
        options=list(region_options.keys()),
        format_func=lambda n: region_options[n],
    )
with col3:
    future_sum = st.number_input("Сумма конкурса, тенге", min_value=0.0, step=100000.0, format="%.0f")

if st.button("🚀 Сгенерировать отчёт", type="primary"):
    if not future_sum:
        st.error("Укажите предполагаемую сумму будущего конкурса.")
    else:
        with st.spinner("Собираю историю подрядчика и формирую прогноз…"):
            try:
                profile = crud.get_supplier_ai_profile(session, selected_bin)
                competitors = crud.list_suppliers_in_region(
                    session, selected_region, exclude_bin=selected_bin
                )
                region_name = region_options[selected_region]
                report_text = ai_assistant.generate_forecast(
                    profile, region_name, selected_region, future_sum, competitors
                )
                new_id = crud.save_ai_report(
                    session, selected_bin, profile["name"], selected_region, region_name,
                    future_sum, report_text, username=st.session_state["username"],
                )
                st.session_state["_ai_selected_report_id"] = new_id
            except ai_assistant.DeepSeekNotConfigured as e:
                st.error(str(e))
            except Exception as e:
                st.error(f"Ошибка генерации отчёта: {e}")

st.divider()
st.subheader("Прогноз")

reports = crud.list_ai_reports(session)
if not reports:
    st.info("Отчётов ещё не создавалось — выберите подрядчика, регион и сумму выше и нажмите «Сгенерировать отчёт».")
else:
    report_ids = [r.id for r in reports]
    default_id = st.session_state.get("_ai_selected_report_id", report_ids[0])
    default_index = report_ids.index(default_id) if default_id in report_ids else 0
    labels = {
        r.id: f"{r.created_at.strftime('%d.%m.%Y %H:%M')} — {r.supplier_name} — "
              f"{r.region_name} — {float(r.future_sum):,.0f} тг ({r.created_by})"
        for r in reports
    }
    selected_id = st.selectbox(
        "Отчёт (доступна вся история сгенерированных прогнозов)",
        options=report_ids,
        format_func=lambda i: labels[i],
        index=default_index,
    )
    selected_report = next(r for r in reports if r.id == selected_id)
    st.markdown(selected_report.report_text)

    created_at_str = selected_report.created_at.strftime("%d.%m.%Y %H:%M")
    docx_bytes = ai_assistant.build_docx_report(
        selected_report.supplier_name, selected_report.region_name,
        float(selected_report.future_sum), created_at_str, selected_report.report_text,
    )
    safe_name = "".join(
        c if c.isalnum() or c in " _-" else "_" for c in selected_report.supplier_name
    ).strip()
    st.download_button(
        "📄 Скачать отчёт в Word",
        data=docx_bytes,
        file_name=f"Прогноз_{safe_name}_{selected_report.created_at.strftime('%Y%m%d_%H%M')}.docx",
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
