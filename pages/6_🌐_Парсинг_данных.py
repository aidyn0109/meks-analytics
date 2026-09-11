import uuid
from pathlib import Path

import pandas as pd
import streamlit as st

import crud
import scraper
from common import get_session

st.title("🌐 Парсинг данных")
st.caption(
    "Поиск новых завершённых конкурсов на портале meks.zakup.sk.kz и скачивание "
    "их протоколов итогов в папку «Загрузки». Файлы **не** сохраняются в базу "
    "автоматически — после проверки загрузите их вручную через страницу «Загрузка PDF»."
)
st.caption(
    "Работает только локально, на компьютере, где настроена ваша ЭЦП: при запуске "
    "откроется окно браузера, и войти на портал нужно будет самостоятельно — "
    "пароль ЭЦП никогда не вводится в это приложение."
)

session = get_session()

st.subheader("Последняя сессия парсинга")
last_session = crud.get_last_scrape_session(session)
if not last_session:
    st.info("Парсинг ещё не запускали.")
else:
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Номер конкурса": r.tender_no,
                    "Название конкурса": r.concurs_name,
                    "Сумма конкурса, тенге": r.total_sum,
                    "Статус": "✅ Скачан" if r.status == "success" else "⚠️ Не скачан",
                    "Причина (если не скачан)": r.reason or "",
                    "Дата парсинга": r.downloaded_at,
                }
                for r in last_session
            ]
        ),
        use_container_width=True,
    )

st.divider()

# Историю выше показываем и на сервере (это просто данные из базы), а вот
# сам запуск там невозможен — см. scraper.is_headless_server.
if scraper.is_headless_server():
    st.error(
        "**Запускать парсинг можно только локально, не на сервере.**\n\n"
        "Сейчас приложение открыто в задеплоенной версии. Для скачивания протоколов "
        "портал требует вход по ЭЦП, а для этого нужно настоящее окно браузера на "
        "вашем компьютере — на сервере нет ни экрана, ни вашей ЭЦП.\n\n"
        "Запустите приложение у себя (`py -m streamlit run app.py`) и откройте этот раздел там. "
        "Все остальные разделы на сервере работают как обычно."
    )
    st.stop()

full_scan = st.checkbox(
    "Полный обход архива (долго)",
    help=(
        "Обычно парсер читает список конкурсов с начала и останавливается, дойдя "
        "до тех, что уже есть в базе — свежие конкурсы всегда в начале списка. "
        "Портал отвечает медленно (до ~30 секунд на страницу), поэтому полный "
        "обход всего архива занимает десятки минут и нужен только при первом "
        "запуске на пустой базе или после долгого перерыва."
    ),
)

status_placeholder = st.empty()

if st.button("🔎 Запустить парсинг", type="primary"):
    try:
        with st.spinner("Ищу завершённые конкурсы на портале…"):
            existing_nos = {t.tender_no for t in crud.list_tenders(session)}
            new_tenders, scan_stats = scraper.find_new_tenders(
                existing_nos,
                full_scan=full_scan,
                status_cb=lambda msg: status_placeholder.info(msg),
            )

        skipped_supervision = scan_stats["skipped_supervision"]
        supervision_note = (
            f" ({skipped_supervision} пропущено как услуги технического надзора)"
            if skipped_supervision
            else ""
        )
        if scan_stats["stopped_early"]:
            st.caption(
                f"Просмотрено {scan_stats['scanned']} конкурсов "
                f"({scan_stats['pages']} стр.) — дальше по списку идут только те, что уже "
                f"есть в базе, поэтому обход остановлен досрочно. Чтобы пройти весь архив, "
                f"включите «Полный обход архива»."
            )

        if not new_tenders:
            st.success(
                f"Новых конкурсов не найдено — проверено {scan_stats['scanned']}, "
                f"остальные уже есть в базе{supervision_note}."
            )
        else:
            st.info(
                f"Найдено {len(new_tenders)} новых конкурсов из {scan_stats['scanned']} "
                f"проверенных{supervision_note}. Сейчас откроется окно браузера — войдите на портале "
                f"через ЭЦП, автоматизация продолжит работу после входа."
            )

            def _status(msg):
                status_placeholder.info(msg)

            downloads_dir = Path.home() / "Downloads"
            downloaded, failed = [], []
            # Ожидание входа занимает до нескольких минут — держать соединение
            # с БД открытым всё это время не нужно (и Neon в какой-то момент
            # обрывает "уснувшее" соединение сам). Отпускаем его перед долгим
            # ожиданием и берём новое, когда оно снова понадобится.
            session.close()
            try:
                downloaded, failed = scraper.download_results_protocols(
                    new_tenders, downloads_dir, status_cb=_status
                )
            except scraper.LoginTimeout as e:
                st.error(str(e))
            except Exception as e:
                st.error(
                    f"Не удалось открыть браузер для входа на портал: {e}. "
                    f"Этот раздел работает только на компьютере с графическим "
                    f"интерфейсом (не на облачном сервере)."
                )
            session = get_session()

            if downloaded or failed:
                run_id = str(uuid.uuid4())
                for d in downloaded:
                    crud.log_scrape_download(
                        session, run_id, d["tender_no"], d["title"], d["total_sum"],
                        filename=d["filename"], status="success",
                    )
                for f in failed:
                    crud.log_scrape_download(
                        session, run_id, f["tender_no"], f["title"], f["total_sum"],
                        status="failed", reason=f["reason"],
                    )
                session.commit()

            if downloaded:
                st.success(f"Скачано протоколов: {len(downloaded)} — сохранены в «{downloads_dir}».")
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "Номер конкурса": d["tender_no"],
                                "Название конкурса": d["title"],
                                "Сумма конкурса, тенге": d["total_sum"],
                                "Файл": d["filename"],
                            }
                            for d in downloaded
                        ]
                    ),
                    use_container_width=True,
                )
            elif new_tenders and not failed:
                st.warning(
                    "Новые конкурсы найдены, но ни один протокол итогов ещё не "
                    "опубликован или не удалось скачать."
                )

            if failed:
                st.warning(f"Не удалось скачать {len(failed)} из {len(new_tenders)} конкурсов:")
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "Номер конкурса": f["tender_no"],
                                "Название конкурса": f["title"],
                                "Сумма конкурса, тенге": f["total_sum"],
                                "Причина": f["reason"],
                            }
                            for f in failed
                        ]
                    ),
                    use_container_width=True,
                )
                st.caption(
                    "Такие конкурсы попробуются заново при следующем запуске парсинга "
                    "(они пока не считаются \"известными\" в базе)."
                )
    except Exception as e:
        session.rollback()
        st.error(f"Ошибка парсинга: {e}")
