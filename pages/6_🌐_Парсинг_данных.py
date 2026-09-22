import scraper
from scraper_page import render_scraper_page

render_scraper_page(
    scan_type="completed",
    portal_status=scraper.STATUS_COMPLETED,
    title="🌐 Парсинг данных",
    subject_accusative="завершённые конкурсы",
    subject_genitive="завершённых конкурсов",
)
