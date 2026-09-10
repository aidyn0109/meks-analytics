"""
Парсер PDF-файлов "Протокол результатов конкурса" (портал электронных
закупок АО «ФНБ «Самрук-Казына»).

Стратегия: pdfplumber.extract_tables() надёжно распознаёт табличные блоки
протокола даже без явных границ ячеек (проверено на образцах). Заголовки
конкурса/заказчика вытаскиваются регулярными выражениями из полного текста
документа, т.к. они не оформлены как таблица.

Результат парсинга — обычный dict/list (без сторонних объектов вроде
datetime), чтобы его можно было напрямую подставлять в Streamlit
data_editor и без проблем сериализовать. Даты и время остаются СТРОКАМИ в
формате, как в протоколе (ДД.ММ.ГГГГ [ЧЧ:ММ]) — в реальные типы БД их
преобразует crud.save_tender при сохранении.
"""

import io
import re
import hashlib

import pdfplumber


def clean(s):
    """Убирает переносы строк и лишние пробелы."""
    if s is None:
        return ""
    return re.sub(r"\s+", " ", str(s).replace("\n", " ")).strip()


# Конкурсы на услуги технического надзора — отдельная категория закупок
# (надзор за работами, а не сами работы), вне профиля парсинга (используется
# и scraper.py при поиске новых конкурсов, и этим модулем/страницей
# "Загрузка PDF" — см. is_technical_supervision). Сравниваем по корням слов,
# а не по точным фразам — так ловятся любые падежные формы ("технического
# надзора", "техническому надзору", "техническим надзором", "технический
# надзор" и т.п.), а не только буквально перечисленные варианты.
# "техническ\w*\s+надзор\w*" покрывает все русские падежи сразу;
# "техник\w*\s+қадаға\w*" — казахский аналог ("техникалық қадағалау").
TECHNICAL_SUPERVISION_RE = re.compile(
    r"техническ\w*\s+надзор\w*"
    r"|надзор\w*\s+техническ\w*"
    r"|техник\w*\s+қадаға\w*",
    re.IGNORECASE,
)


def is_technical_supervision(text: str) -> bool:
    """True, если текст — услуги технического надзора (в любой падежной
    форме, рус./каз.). Название конкурса не всегда это выдаёт (бывают
    нейтральные названия) — но перечень закупаемых работ в самом протоколе
    (Lot.name после разбора PDF) обычно называет услугу прямо, поэтому его
    и стоит проверять этой же функцией на странице "Загрузка PDF"."""
    return bool(TECHNICAL_SUPERVISION_RE.search(text or ""))


def to_number(v):
    if v is None:
        return None
    v = str(v).strip().replace(" ", "").replace(",", ".")
    if v == "" or v.lower() in ("нет", "да"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def file_hash(file_bytes: bytes) -> str:
    return hashlib.sha256(file_bytes).hexdigest()


def _find_bid(data, bin_):
    for b in data["bids"]:
        if b["bin"] == bin_:
            return b
    return None


def _ensure_bid(data, bin_, name=None):
    bin_ = clean(bin_)
    if not bin_:
        return None
    name = clean(name) if name else ""
    b = _find_bid(data, bin_)
    if b is None:
        b = {
            "bin": bin_,
            "name": name,
            "status": None,
            "submitted_at": "",
            "offered_price": None,
            "is_winner": False,
            "is_second_place": False,
            "rejection_reason": "",
            "criteria": [],
        }
        data["bids"].append(b)
    elif name and len(name) > len(b.get("name") or ""):
        # Одно и то же наименование поставщика встречается в нескольких
        # таблицах протокола; если оно длинное, в одной из них оно может
        # попасть обрезанным (перенос строки/ячейки, см. _parse_tender_pdf) —
        # берём более длинный (полный) вариант, откуда бы он ни пришёл.
        b["name"] = name
    return b


def _is_alt_template(full_text: str) -> bool:
    """
    Портал печатает протоколы в двух разных шаблонах с одинаковым набором
    данных: классический ("ПРОТОКОЛ РЕЗУЛЬТАТОВ КОНКУРСА №...") и более новый
    ("Протокол об итогах конкурса (№...)"), у которого другие подписи полей
    в шапке и в таблице критериев ("Наименование потенциального поставщика"
    вместо "Наименование", "БИН (ИИН)/ИНН/УНП" вместо "БИН").
    """
    return "Протокол об итогах конкурса" in full_text


def _parse_header_alt(full_text: str, data: dict):
    """Шапка альтернативного шаблона ("Протокол об итогах конкурса (№...)")."""
    m = re.search(r"Протокол об итогах конкурса\s*\((\S+)\)", full_text)
    if m:
        data["tender_no"] = m.group(1).strip()

    m = re.search(r"Дата и время\s*:?\s*(\d{2}\.\d{2}\.\d{4})\s+(\d{2}:\d{2})", full_text)
    if m:
        data["protocol_date"] = m.group(1)
        data["protocol_time"] = m.group(2)

    # В этом шаблоне подпись "Адрес заказчика" может напечататься не перед
    # своим значением, а "наехать" на вторую строку названия конкурса, если
    # оно переносится на несколько строк (аналог смещения в основном
    # шаблоне, только с другой подписью-жертвой). Значение адреса при этом
    # всегда идёт последним перед "Состав конкурсной комиссии:" и начинается
    # со слова "КАЗАХСТАН" — это надёжный якорь независимо от того, куда
    # уехала подпись поля.
    m = re.search(r"Заказчик\*?\s*:?\s*(.*?)\s*Состав конкурсной комиссии\s*:", full_text, re.DOTALL)
    if not m:
        return
    block = m.group(1)

    addr_idx = block.rfind("КАЗАХСТАН")
    if addr_idx != -1:
        data["customer_address"] = clean(block[addr_idx:])
        block = block[:addr_idx]

    for label in ("Конкурс № :", "Конкурс №:", "Конкурс №", "Название конкурса:",
                  "Название конкурса", "Адрес заказчика:", "Адрес заказчика"):
        block = block.replace(label, " ")
    block = clean(block)

    tender_no = data.get("tender_no") or ""
    if tender_no and tender_no in block:
        before, _, after = block.partition(tender_no)
        data["customer_name"] = clean(before)
        data["title"] = clean(after)
    else:
        data["customer_name"] = block


def _parse_header(full_text: str, data: dict):
    if _is_alt_template(full_text):
        _parse_header_alt(full_text, data)
        return

    m = re.search(r"ПРОТОКОЛ РЕЗУЛЬТАТОВ КОНКУРСА\s*№\s*(\S+)", full_text)
    if m:
        data["tender_no"] = m.group(1).strip()

    m = re.search(r"Дата и время:\s*(\d{2}\.\d{2}\.\d{4})\s+(\d{2}:\d{2})", full_text)
    if m:
        data["protocol_date"] = m.group(1)
        data["protocol_time"] = m.group(2)

    # Адрес заказчика печатается отдельной надёжной строкой ещё раз в самом
    # начале документа (сразу после номера протокола, до "Дата и время:") —
    # берём его отсюда, т.к. копия ниже (в блоке подписей полей) часто
    # "расходится" с названием конкурса, см. комментарий ниже.
    m = re.search(r"Адрес:\s*(.*?)\s*Дата и время\s*:", full_text, re.DOTALL)
    reliable_address = clean(m.group(1)) if m else ""
    if reliable_address:
        data["customer_address"] = reliable_address

    # Блок "Заказчик: ... Состав конкурсной комиссии:" в шаблоне портала —
    # это форма из подписанных полей (Заказчик / Конкурс № / Название
    # конкурса / Адрес заказчика), НО если значение поля "Заказчик" (частый
    # случай — длинное официальное наименование ГКП/ГУ) переносится на
    # несколько строк, pdfplumber извлекает текст "лесенкой": подпись
    # следующего поля печатается на своей строке как обычно, а вот её
    # РЕАЛЬНОЕ значение уезжает ещё на 1+ строки вниз. В итоге номер
    # конкурса из "Конкурс № :" может подхватить хвост наименования
    # заказчика, а под "Название конкурса:" может оказаться сам номер —
    # именно так в БД попадали куски "Заказчик" вместо номера конкурса.
    #
    # Устойчивый способ обойти это смещение: номер конкурса уже надёжно
    # известен из заголовка протокола выше ("ПРОТОКОЛ РЕЗУЛЬТАТОВ КОНКУРСА
    # №..." печатается одной строкой и смещению не подвержен). Берём блок
    # целиком, вычищаем из него подписи полей и уже известный адрес, и
    # разрезаем получившийся текст по ФАКТИЧЕСКОМУ вхождению номера
    # конкурса: всё до него — заказчик, всё после — название конкурса.
    # Работает независимо от того, на сколько строк и какое именно поле
    # успело "уехать" — в отличие от расчёта по фиксированным подписям.
    m = re.search(r"Заказчик:\s*(.*?)\s*Состав конкурсной комиссии:", full_text, re.DOTALL)
    if m:
        block = m.group(1)
        for label in ("Конкурс № :", "Конкурс №:", "Название конкурса:", "Адрес заказчика:"):
            block = block.replace(label, " ")
        block = clean(block)

        tender_no = data.get("tender_no") or ""
        if tender_no and tender_no in block:
            before, _, after = block.partition(tender_no)
            data["customer_name"] = clean(before)
            if reliable_address:
                after = after.replace(reliable_address, " ")
            data["title"] = clean(after)
        else:
            # Номер не нашёлся в блоке дословно (нетиповое форматирование) —
            # заказчика всё равно сохраняем, чтобы не потерять данные,
            # но название конкурса в этом случае не разбираем.
            data["customer_name"] = block

    if not data.get("customer_address"):
        m = re.search(
            r"Адрес заказчика:\s*(.*?)\s*Состав конкурсной комиссии:", full_text, re.DOTALL
        )
        if m:
            data["customer_address"] = clean(m.group(1))


def _merge_paginated_tables(all_tables):
    """
    pdfplumber отдаёт отдельный объект таблицы на каждой странице, даже если
    визуально это одна таблица, разорванная переносом страницы (например,
    "Отклонённые заявки" с длинными причинами на несколько поставщиков).
    Настоящие заголовки таблиц в этом шаблоне всегда начинаются с "№" в первой
    колонке. Если у очередной таблицы то же количество колонок, что и у
    предыдущей, а её первая строка не похожа на заголовок (не начинается с
    "№") — считаем её продолжением предыдущей таблицы и просто дописываем
    её строки (без заголовка, там его и нет).
    """
    merged = []
    for table in all_tables:
        if not table or not table[0]:
            continue
        prev = merged[-1] if merged else None
        prev_is_numbered = bool(prev) and clean(prev[0][0]) == "№"
        this_is_header = clean(table[0][0]) == "№"
        if prev_is_numbered and not this_is_header and len(table[0]) == len(prev[0]):
            merged[-1].extend(table)
        else:
            merged.append(list(table))
    return merged


def parse_tender_pdf(file_bytes: bytes) -> dict:
    data = {
        "tender_no": "",
        "title": "",
        "customer_name": "",
        "customer_address": "",
        "protocol_date": "",
        "protocol_time": "",
        "lots": [],
        "commission_members": [],
        "bids": [],
    }

    if isinstance(file_bytes, (bytes, bytearray)):
        file_bytes = io.BytesIO(file_bytes)

    with pdfplumber.open(file_bytes) as pdf:
        full_text = "\n".join(p.extract_text() or "" for p in pdf.pages)
        all_tables = []
        for page in pdf.pages:
            all_tables.extend(page.extract_tables())
        all_tables = _merge_paginated_tables(all_tables)

    _parse_header(full_text, data)

    for table in all_tables:
        if not table or not table[0]:
            continue
        header_join = " ".join(clean(h) for h in table[0])

        # --- Состав конкурсной комиссии ---
        if "Ф.И.О." in header_join and "Должность" in header_join:
            for row in table[1:]:
                if not row or not clean(row[1] if len(row) > 1 else None):
                    continue
                data["commission_members"].append(
                    {
                        "full_name": clean(row[1]),
                        "position": clean(row[2]) if len(row) > 2 else "",
                        "role": clean(row[3]) if len(row) > 3 else "",
                    }
                )

        # --- Критерии оценки заявки (Предложения потенциальных поставщиков) ---
        elif "Наименование поля" in header_join:
            bin_ = None
            name_ = None
            criteria = []
            last_criterion = None
            for row in table[1:]:
                if not row or len(row) < 3:
                    continue
                field = clean(row[1])
                value = clean(row[2])
                score = to_number(row[3]) if len(row) > 3 else None
                # В альтернативном шаблоне эти подписи длиннее ("Наименование
                # потенциального поставщика", "БИН (ИИН)/ИНН/УНП") — сравниваем
                # по началу строки, а не точным совпадением.
                if field.startswith("Наименование"):
                    name_ = value
                    continue
                if field.startswith("БИН"):
                    # Если наименование поставщика слишком длинное (не влезло
                    # в одну строку целиком), его хвост иногда "наезжает" на
                    # эту же строку вместо самого БИН — тогда тут не число, а
                    # обрывок названия, а настоящий БИН окажется в следующей
                    # строке-обрывке (пустое поле, только число в значении).
                    if value and not re.fullmatch(r"\d{9,14}", value):
                        name_ = ((name_ or "") + " " + value).strip()
                        continue
                    bin_ = value
                    continue
                if not field:
                    if not bin_ and value and re.fullmatch(r"\d{9,14}", value):
                        bin_ = value
                        continue
                    # продолжение значения предыдущего критерия, перенесённое
                    # на следующую страницу разрывом высокой ячейки таблицы
                    if last_criterion is not None and value:
                        last_criterion["value"] = (
                            (last_criterion["value"] or "") + " " + value
                        ).strip()
                    continue
                crit = {"name": field, "value": value or None, "score": score}
                criteria.append(crit)
                last_criterion = crit
            if bin_:
                bid = _ensure_bid(data, bin_, name=name_)
                # Альтернативный шаблон повторяет те же критерии дважды —
                # один раз в "Предложения потенциальных поставщиков", второй
                # раз в "Расчёт баллов участников конкурса" с переформулиро-
                # ванными названиями полей. Берём только первое вхождение
                # (стабильные названия критериев важны для агрегации в Power
                # BI), второе не должно перезатирать уже сохранённое.
                if not bid.get("criteria"):
                    bid["criteria"] = criteria
                if bid.get("status") is None:
                    bid["status"] = "Допущен"

        # --- Отклонённые заявки ---
        elif "Требование" in header_join and "Причина" in header_join:
            last_bid = None
            for row in table[1:]:
                if not row:
                    continue
                reason_fragment = clean(row[4]) if len(row) > 4 else ""
                if not clean(row[1] if len(row) > 1 else None):
                    # продолжение "Причины" предыдущего поставщика, перенесённое
                    # на следующую страницу разрывом высокой ячейки таблицы —
                    # это не новый поставщик
                    if last_bid is not None and reason_fragment:
                        last_bid["rejection_reason"] = (
                            (last_bid.get("rejection_reason") or "") + " " + reason_fragment
                        ).strip()
                    continue
                bin_ = clean(row[2]) if len(row) > 2 else ""
                bid = _ensure_bid(data, bin_, name=row[1])
                if bid:
                    bid["status"] = "Отклонён"
                    if reason_fragment:
                        bid["rejection_reason"] = (
                            (bid.get("rejection_reason") or "") + " " + reason_fragment
                        ).strip()
                    last_bid = bid

        # --- Заявки с датой/временем подачи ---
        elif "Дата и время представления заявки" in header_join:
            for row in table[1:]:
                if not row or not clean(row[1] if len(row) > 1 else None):
                    continue
                bin_ = clean(row[2]) if len(row) > 2 else ""
                bid = _ensure_bid(data, bin_, name=row[1])
                if bid and len(row) > 3:
                    bid["submitted_at"] = clean(row[3])

        # --- Лоты / перечень закупаемых работ ---
        elif "Цена за единицу" in header_join:
            for row in table[1:]:
                if not row or not clean(row[1] if len(row) > 1 else None):
                    continue
                data["lots"].append(
                    {
                        "name": clean(row[1]),
                        "quantity": to_number(row[2]) if len(row) > 2 else None,
                        "unit_price": to_number(row[3]) if len(row) > 3 else None,
                        "allocated_amount": to_number(row[4]) if len(row) > 4 else None,
                    }
                )

        # --- Победитель ---
        elif "Наименование поставщика победителя" in header_join:
            if len(table) > 1 and table[1] and clean(table[1][0]):
                bin_ = clean(table[1][1]) if len(table[1]) > 1 else ""
                bid = _ensure_bid(data, bin_, name=table[1][0])
                if bid:
                    bid["is_winner"] = True
                    if bid.get("status") is None:
                        bid["status"] = "Допущен"

        # --- Второе место ---
        elif "занявшего второе место" in header_join:
            if len(table) > 1 and table[1] and clean(table[1][0]):
                bin_ = clean(table[1][1]) if len(table[1]) > 1 else ""
                bid = _ensure_bid(data, bin_, name=table[1][0])
                if bid:
                    bid["is_second_place"] = True

        # --- Список допущенных заявок (страховочно проставляем статус) ---
        # Заголовок отличается между шаблонами ("№ Наименование БИН..." /
        # "№ Наименование потенциального поставщика БИН...") — таблица
        # всегда ровно из 3 колонок (№, наименование, БИН), этого достаточно
        # для однозначного распознавания без привязки к точной формулировке.
        elif (
            len(table[0]) == 3
            and "Наименование" in header_join
            and "БИН" in header_join
        ):
            for row in table[1:]:
                if not row or not clean(row[1] if len(row) > 1 else None):
                    continue
                bin_ = clean(row[2]) if len(row) > 2 else ""
                bid = _ensure_bid(data, bin_, name=row[1])
                if bid and bid.get("status") is None:
                    bid["status"] = "Допущен"

    # Заявки без определённого статуса (не встретились ни в "допущен", ни в
    # "отклонён" таблицах, но есть баллы) — считаем допущенными по умолчанию.
    for b in data["bids"]:
        if b.get("status") is None:
            b["status"] = "Допущен"

    return data
