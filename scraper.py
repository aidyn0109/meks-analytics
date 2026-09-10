"""
Полуавтоматический сбор новых протоколов итогов конкурса с портала
meks.zakup.sk.kz.

Стратегия:
- Список конкурсов и карточка конкретного конкурса (включая перечень
  протоколов) отдаются публичным API портала без входа в личный кабинет —
  используем это для поиска и сравнения с базой без всякой авторизации.
- Сам файл протокола отдаётся только авторизованным пользователям. Портал
  требует вход по ЭЦП, которая физически привязана к компьютеру
  пользователя (через локальную службу NCALayer) — этот код НИКОГДА не
  просит и не хранит пароль ЭЦП. Вместо этого открывается настоящее окно
  браузера, пользователь входит в него сам, как обычно, а автоматизация
  продолжает работу в этом же (уже авторизованном) браузере.

Сайт использует сертификат национального удостоверяющего центра Казахстана,
который не входит в стандартный доверенный список — везде ниже отключена
проверка TLS-сертификата (verify=False / ignore_https_errors), это
осознанный выбор, а не недосмотр.
"""

import random
import time
from pathlib import Path

import requests
import urllib3

from parser import is_technical_supervision

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Пауза между запросами в "тесном" цикле постраничного списка конкурсов —
# сама по себе не спасает от 429 полностью, но сильно снижает шанс на него
# нарваться, когда страниц набирается много.
_POLITE_DELAY = 0.25


def _get_with_retry(url, *, max_attempts=5, backoff_base=1.6, **kwargs):
    """
    requests.get с повтором при HTTP 429 (портал ограничивает частоту
    запросов) и временных 502/503/504 — без этого разовый всплеск
    активности мог полностью оборвать парсинг посреди работы с ошибкой
    "429". Уважает заголовок Retry-After, если сервер его прислал; иначе —
    экспоненциальная пауза со случайным разбросом (джиттер), чтобы повторные
    запросы не били "в такт" и не накладывались друг на друга.
    """
    kwargs.setdefault("verify", False)
    kwargs.setdefault("timeout", 30)
    last_exc = None
    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.get(url, **kwargs)
        except requests.RequestException as e:
            last_exc = e
            resp = None
        else:
            if resp.status_code not in (429, 502, 503, 504):
                return resp
            last_exc = requests.HTTPError(f"{resp.status_code} для {url}", response=resp)

        if attempt == max_attempts:
            break

        retry_after = resp.headers.get("Retry-After") if resp is not None else None
        try:
            wait = float(retry_after) if retry_after else backoff_base**attempt
        except ValueError:
            wait = backoff_base**attempt
        time.sleep(wait + random.uniform(0, 0.5))

    raise last_exc


BASE_URL = "https://meks.zakup.sk.kz"
LIST_API = f"{BASE_URL}/api/pub/application-announcement-311/list"
PROTOCOLS_API = f"{BASE_URL}/api/pub/application-announcement-311/all-protocols"
LOGIN_URL = f"{BASE_URL}/auth/login"


def fetch_completed_announcements(page_size=50, max_pages=500):
    """
    Постранично собирает все конкурсы со статусом "Конкурс завершён" через
    публичный (не требующий входа) API портала.
    """
    results = []
    page = 0
    while page < max_pages:
        resp = _get_with_retry(
            LIST_API,
            params={"status": "COMPETITION_COMPLETED", "page": page, "size": page_size},
        )
        resp.raise_for_status()
        data = resp.json()
        for item in data.get("content", []):
            results.append(
                {
                    "announcement_id": item.get("id"),
                    "tender_no": (item.get("concursNum") or "").strip(),
                    "title": (item.get("concursName") or "").strip(),
                    "customer_name": (item.get("customerName") or "").strip(),
                    "total_sum": (item.get("data") or {}).get("totalSumWithoutNDS"),
                }
            )
        if data.get("last", True):
            break
        page += 1
        time.sleep(_POLITE_DELAY)
    return results


def find_new_tenders(announcements, existing_tender_nos):
    """
    Конкурсы из announcements, номера которых ещё нет в базе
    (existing_tender_nos), за исключением услуг технического надзора — они
    вне профиля закупок и не должны попадать в парсинг/скачивание.

    Фильтр — только по названию самого конкурса (см. is_technical_supervision)
    и без единого дополнительного запроса на кандидата: раньше здесь ещё
    донабирали по полю "Наименование ЕНС ТРУ" карточки конкурса отдельным
    запросом на каждого нового кандидата — при большом числе новых конкурсов
    за день это давало десятки лишних запросов подряд и упирало парсинг в
    лимит частоты запросов портала (HTTP 429), обрывая его на середине.

    Часть конкурсов технадзора с нейтральным названием всё равно проскочит
    сюда — это ожидаемо и не страшно: их протокол всё равно скачается и
    попадёт на проверку человеком на странице "Загрузка PDF", а не сразу
    в базу — там та же самая проверка (is_technical_supervision) применяется
    к перечню закупаемых работ уже из самого протокола (см. parser.py) и
    показывает явное предупреждение перед сохранением.

    Возвращает (new_tenders, skipped_supervision) — второе число (сколько
    отсеяно по названию) для отображения в интерфейсе.
    """
    existing = set(existing_tender_nos)
    seen = set()
    new_ones = []
    skipped_supervision = 0
    for a in announcements:
        no = a["tender_no"]
        if not no or no in existing or no in seen:
            continue
        if is_technical_supervision(a["title"]):
            skipped_supervision += 1
            continue
        seen.add(no)
        new_ones.append(a)
    return new_ones, skipped_supervision


def get_results_protocol(announcement_id):
    """
    Описание документа "Протокол итогов" для конкурса (через публичный API),
    или None, если он ещё не опубликован.
    """
    resp = _get_with_retry(PROTOCOLS_API, params={"announcementId": announcement_id})
    resp.raise_for_status()
    for doc in resp.json():
        if doc.get("documentSubType") == "RESULTS":
            return doc
    return None


class LoginTimeout(Exception):
    """Пользователь не успел войти на сайт за отведённое время."""


def download_results_protocols(new_tenders, downloads_dir, login_wait_timeout=300, status_cb=None):
    """
    Открывает видимое окно браузера на странице входа портала и ждёт, пока
    пользователь войдёт сам (по ЭЦП или QR) — эта функция никогда не
    подставляет и не видит пароль ЭЦП. После успешного входа скачивает
    "Протокол итогов" для каждого конкурса из new_tenders в downloads_dir.

    new_tenders: список dict с ключами announcement_id, tender_no, title,
    total_sum (формат, который возвращает find_new_tenders).
    status_cb: необязательная функция(str) для промежуточных сообщений о
    прогрессе (например, чтобы показать их в интерфейсе Streamlit).

    Возвращает (downloaded, failed) — по каждому конкурсу из new_tenders
    он попадает ровно в один из двух списков:
    - downloaded: {tender_no, title, total_sum, filename}
    - failed: {tender_no, title, total_sum, reason} — почему не скачался.
    Бросает LoginTimeout, если вход не произошёл за login_wait_timeout секунд.
    """
    from playwright.sync_api import sync_playwright

    def _status(msg):
        if status_cb:
            status_cb(msg)

    downloaded = []
    failed = []

    def _skip(t, reason):
        failed.append(
            {
                "tender_no": t["tender_no"],
                "title": t["title"],
                "total_sum": t["total_sum"],
                "reason": reason,
            }
        )
        _status(f"№{t['tender_no']}: {reason}")

    Path(downloads_dir).mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=["--ignore-certificate-errors"])
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()
        page.goto(LOGIN_URL, wait_until="networkidle", timeout=30000)

        _status("Войдите на портале через ЭЦП в открывшемся окне браузера…")

        # Раньше здесь ЕЩЁ проверялось наличие текста "Вход по ЭЦП" на
        # странице — это ложно держало цикл в состоянии "не вошли" даже
        # после успешного входа, если Angular-роутер не выгружал компонент
        # формы входа из DOM полностью (просто скрывал). Ориентир теперь —
        # переход с /auth/login и, запасным вариантом, токен в localStorage.
        # Если это ЕЩЁ не сработает — на таймауте сохраняем скриншот и полную
        # историю URL, чтобы понять реальную причину не вслепую.
        deadline = time.time() + login_wait_timeout
        logged_in = False
        last_notice = 0
        current_url = LOGIN_URL
        url_history = [(0, current_url)]
        while time.time() < deadline:
            try:
                current_url = page.url
                left_login_page = "/auth/login" not in current_url
                has_token = page.evaluate(
                    "() => Object.keys(localStorage).some(k => "
                    "/token/i.test(k) && !!localStorage.getItem(k))"
                )
            except Exception:
                left_login_page = False
                has_token = False
            elapsed = int(time.time() - (deadline - login_wait_timeout))
            if url_history[-1][1] != current_url:
                url_history.append((elapsed, current_url))
            if left_login_page or has_token:
                logged_in = True
                break
            if elapsed - last_notice >= 20:
                last_notice = elapsed
                _status(
                    f"Жду вход на портале… ({elapsed} c из {login_wait_timeout}). "
                    f"Сейчас в браузере открыт: {current_url}"
                )
            time.sleep(1)

        if not logged_in:
            try:
                keys = page.evaluate("() => Object.keys(localStorage)")
            except Exception:
                keys = []
            screenshot_path = str(Path(downloads_dir) / "_debug_login_timeout.png")
            try:
                page.screenshot(path=screenshot_path, full_page=True)
            except Exception:
                screenshot_path = None
            browser.close()
            raise LoginTimeout(
                f"Не дождался входа на сайт за {login_wait_timeout} секунд — попробуйте снова. "
                f"Диагностика — URL на момент таймаута: {current_url}; "
                f"история переходов (сек, url): {url_history}; "
                f"ключи localStorage: {keys}"
                + (f"; скриншот сохранён в {screenshot_path}" if screenshot_path else "")
            )

        _status(f"Вход выполнен, скачиваю протоколы (0/{len(new_tenders)})…")

        for i, t in enumerate(new_tenders, start=1):
            # Любая ошибка на отдельном конкурсе (не открылась строка, не
            # успело всплывающее окно перейти на файл и т.п.) не должна
            # обрывать весь пакет — пропускаем этот конкурс и идём дальше.
            try:
                doc = get_results_protocol(t["announcement_id"])
                if not doc:
                    _skip(t, "протокол итогов ещё не опубликован")
                    continue

                # На части конкурсов клик по "Скачать" не запускал ни одного
                # сетевого запроса — похоже на разовый сбой отрисовки
                # страницы (Angular иногда догружает таблицу протоколов уже
                # ПОСЛЕ networkidle). Прежде чем сдаться, пробуем ещё раз с
                # чистой перезагрузкой страницы конкурса.
                pdf_bytes = None
                last_reason = "неизвестная причина"
                max_attempts = 2
                for attempt in range(1, max_attempts + 1):
                    if attempt > 1:
                        _status(f"№{t['tender_no']}: повторная попытка ({attempt}/{max_attempts})…")
                        time.sleep(2)

                    page.goto(
                        f"{BASE_URL}/board/announcement-view/{t['announcement_id']}",
                        wait_until="networkidle",
                        timeout=30000,
                    )
                    # "networkidle" гарантирует, что закончились запросы, но
                    # не что таблица протоколов уже отрисована — без этого
                    # клик мог попадать по строке из не обновившегося DOM.
                    try:
                        page.wait_for_selector("text=Протоколы", timeout=10000)
                    except Exception:
                        pass

                    rows = page.locator("table tr")
                    target_row = None
                    for r in range(rows.count()):
                        text = rows.nth(r).inner_text()
                        if "Протокол итогов" in text and "ПКО" not in text:
                            target_row = rows.nth(r)
                            break
                    if target_row is None:
                        last_reason = "не нашёл строку «Протокол итогов» на странице конкурса"
                        continue

                    # Слушаем ОДНОВРЕМЕННО два возможных механизма — какой из
                    # них сработает, зависит от того, как фронтенд отдаёт
                    # файл после входа (без входа увидеть это было нельзя):
                    # 1) настоящее браузерное скачивание (событие
                    #    "download") — для него Playwright сам перехватывает
                    #    файл, обычный "response" тела в этом случае не отдаёт;
                    # 2) файл открывается как ответ на fetch/навигацию
                    #    (событие "response" с этим же URL) — тогда читаем
                    #    resp.body().
                    # Заодно копим ВСЕ увиденные url — если снова ничего не
                    # поймаем, это покажет, был ли запрос вообще.
                    # ВАЖНО: тело ответа читаем СРАЗУ внутри обработчика
                    # события "response", а не позже (после выхода из цикла
                    # ожидания) — если между получением ответа и повторным
                    # обращением к нему страница успевала перейти дальше
                    # (например, всплывающее окно САМО закрывалось или
                    # перенаправлялось), Playwright роняет resp.body() с
                    # "Response body is not available for a response that
                    # was navigated away from". Внутри самого события ответ
                    # гарантированно ещё жив.
                    captured = {"pdf_bytes": None, "status": None, "download": None}
                    opened_pages = [page]
                    seen_urls = []

                    def _on_response(resp, _captured=captured, _seen=seen_urls):
                        _seen.append(resp.url)
                        if _captured["pdf_bytes"] is not None or _captured["download"] is not None:
                            return
                        if "announcement-documents" not in resp.url or "download" not in resp.url:
                            return
                        if resp.status != 200:
                            _captured["status"] = resp.status
                            return
                        try:
                            _captured["pdf_bytes"] = resp.body()
                        except Exception:
                            pass

                    def _on_download(dl, _captured=captured):
                        if _captured["download"] is None:
                            _captured["download"] = dl

                    def _on_popup(p, _pages=opened_pages, _on_dl=_on_download):
                        _pages.append(p)
                        p.on("download", _on_dl)

                    context.on("response", _on_response)
                    context.on("page", _on_popup)
                    page.on("download", _on_download)
                    try:
                        target_row.get_by_text("Скачать").click()
                        deadline_dl = time.time() + 20
                        while time.time() < deadline_dl and captured["pdf_bytes"] is None \
                                and captured["download"] is None and captured["status"] is None:
                            time.sleep(0.2)
                    finally:
                        context.remove_listener("response", _on_response)
                        context.remove_listener("page", _on_popup)
                        page.remove_listener("download", _on_download)

                    def _close_opened_pages():
                        for p in opened_pages:
                            if p is not page:
                                try:
                                    p.close()
                                except Exception:
                                    pass

                    if captured["download"] is not None:
                        dl = captured["download"]
                        tmp_path = dl.path()
                        pdf_bytes_candidate = Path(tmp_path).read_bytes() if tmp_path else b""
                        _close_opened_pages()
                        if not pdf_bytes_candidate:
                            last_reason = "скачивание началось, но файл оказался пустым"
                            continue
                        pdf_bytes = pdf_bytes_candidate
                        break
                    elif captured["pdf_bytes"] is not None:
                        pdf_bytes = captured["pdf_bytes"]
                        _close_opened_pages()
                        break
                    elif captured["status"] is not None:
                        _close_opened_pages()
                        last_reason = f"сервер вернул код {captured['status']} при скачивании"
                        continue
                    else:
                        _close_opened_pages()
                        sample = seen_urls[-8:]
                        last_reason = (
                            f"не дождался файла за 20 с (попытка {attempt}/{max_attempts}; "
                            f"запросов после клика: {len(seen_urls)}; последние: {sample})"
                        )
                        continue

                if pdf_bytes is None:
                    _skip(t, last_reason)
                    continue

                filename = doc.get("filename") or f"{t['tender_no']}.pdf"
                safe_name = f"{t['tender_no']}_{filename}".replace("/", "_").replace("\\", "_")
                dest = Path(downloads_dir) / safe_name
                dest.write_bytes(pdf_bytes)

                downloaded.append(
                    {
                        "tender_no": t["tender_no"],
                        "title": t["title"],
                        "total_sum": t["total_sum"],
                        "filename": safe_name,
                    }
                )
                _status(f"Скачано ({i}/{len(new_tenders)}): №{t['tender_no']}")
            except Exception as e:
                _skip(t, f"ошибка «{e}»")
                continue

        browser.close()

    return downloaded, failed
