import scraper
from scraper_page import render_scraper_page

render_scraper_page(
    scan_type="failed",
    portal_status=scraper.STATUS_FAILED,
    title="🚫 Парсинг несостоявшихся",
    subject_accusative="несостоявшиеся конкурсы",
    subject_genitive="несостоявшихся конкурсов",
)
