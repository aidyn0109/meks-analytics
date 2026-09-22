"""
Функции работы с базой данных: сохранение распарсенных/введённых вручную
данных, чтение для экрана правки, запись в журнал изменений (audit_log).

Все функции принимают/возвращают обычные dict/list — тот же "плоский"
формат, что отдаёт parser.py и что рисует ui_components.render_tender_editor,
чтобы Upload / Manual entry / Edit страницы использовали один и тот же код
сохранения без дублирования логики.
"""

import re
from datetime import datetime, date
from decimal import Decimal

from sqlalchemy import select, delete, func, case, or_
from sqlalchemy.orm import selectinload

from models import (
    Tender, Lot, CommissionMember, Supplier, Bid, BidCriterion, AuditLog,
    ScrapeDownload, Customer, AIReport,
)


# Тендер тянет за собой 3 коллекции (лоты, комиссия, заявки) плюс, для
# каждой заявки, поставщика и критерии. Без selectinload это лениво
# подгружается по одному запросу на заявку — 2 запроса x N заявок к
# отдельной (сетевой, не локальной) БД, что на практике оборачивается
# секундами задержки. selectinload вместо этого делает по одному
# батч-запросу на каждую коллекцию, независимо от числа строк.
_TENDER_LOAD_OPTIONS = (
    selectinload(Tender.lots),
    selectinload(Tender.commission_members),
    selectinload(Tender.bids).selectinload(Bid.supplier),
    selectinload(Tender.bids).selectinload(Bid.criteria),
)


def _get_tender_full(session, tender_no: str):
    """Тендер со всеми дочерними записями, загруженными батчами (см. выше)."""
    return session.execute(
        select(Tender).where(Tender.tender_no == tender_no).options(*_TENDER_LOAD_OPTIONS)
    ).scalar_one_or_none()


# ---------- преобразование типов ----------

def _s(v):
    """Текстовое значение поля формы. None и NaN (pandas) -> ''."""
    if v is None:
        return ""
    if isinstance(v, float) and v != v:  # NaN
        return ""
    return str(v)


def _to_float(v):
    if v is None or v == "":
        return None
    # pandas data_editor подставляет NaN в пустые числовые ячейки; NaN != NaN,
    # поэтому его нельзя пропускать дальше — он ломает и БД, и JSON журнала.
    if isinstance(v, float) and v != v:
        return None
    try:
        f = float(str(v).replace(" ", "").replace(",", "."))
    except (ValueError, TypeError):
        return None
    if f != f:  # результат тоже мог оказаться NaN (напр. из строки "nan")
        return None
    return f


def _parse_date(s):
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _parse_datetime_combo(date_str, time_str=None):
    d = _parse_date(date_str)
    if not d:
        return None
    if time_str:
        m = re.search(r"(\d{1,2}):(\d{2})", str(time_str))
        if m:
            return datetime(d.year, d.month, d.day, int(m.group(1)), int(m.group(2)))
    return datetime(d.year, d.month, d.day)


def _parse_datetime_str(s):
    """Парсит одну строку вида '24.06.2026 12:18' или '24.06.2026'."""
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _ser(v):
    """Сериализация значения для JSON-колонки журнала изменений."""
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, Decimal):
        # Decimal('NaN') существует; в JSON его быть не должно.
        return None if v.is_nan() else float(v)
    if isinstance(v, float) and v != v:  # NaN
        return None
    return v


# ---------- чтение ----------

def list_lot_categories(session):
    """Все уникальные категории работ, когда-либо сохранённые в лотах —
    для выпадающего списка на форме, вместе со стандартными категориями."""
    rows = session.execute(select(Lot.category).where(Lot.category.isnot(None)).distinct())
    return sorted({(c or "").strip() for (c,) in rows if (c or "").strip()})


def list_suppliers_with_regions(session):
    """Все поставщики с их регионом (если уже задан) — вкладка "Региональность"."""
    suppliers = session.execute(select(Supplier).order_by(Supplier.name)).scalars().all()
    return [
        {
            "bin": s.bin,
            "name": (s.name or "").strip() or s.bin,
            "region_number": s.region_number,
            "region_name": s.region_name,
        }
        for s in suppliers
    ]


def get_or_create_customer(session, name: str) -> "Customer | None":
    """
    Находит заказчика по точному названию (после strip) или создаёт нового.
    Никогда не трогает region_code/region_name уже существующего заказчика —
    повторная загрузка тендера того же заказчика не должна ни плодить
    дубликаты в customers, ни очищать уже проставленный регион.
    """
    name = (name or "").strip()
    if not name:
        return None
    customer = session.execute(
        select(Customer).where(Customer.name == name)
    ).scalar_one_or_none()
    if customer is None:
        customer = Customer(name=name)
        session.add(customer)
        session.flush()  # нужен id, если тендер сохраняют дальше в этой же транзакции
    return customer


def list_customers_with_regions(session):
    """Все заказчики из справочника customers с их регионом — для вкладки
    "Региональность"."""
    customers = session.execute(select(Customer).order_by(Customer.name)).scalars().all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "region_number": c.region_code,
            "region_name": c.region_name,
        }
        for c in customers
    ]


def list_region_catalog(session):
    """Уникальные пары (номер, название) региона, уже встречавшиеся хоть у
    поставщика, хоть у заказчика — общий справочник для выпадающих списков
    на вкладке "Региональность"."""
    pairs = list(
        session.execute(
            select(Supplier.region_number, Supplier.region_name).where(
                Supplier.region_number.isnot(None)
            )
        )
    ) + list(
        session.execute(
            select(Customer.region_code, Customer.region_name).where(
                Customer.region_code.isnot(None)
            )
        )
    )
    seen = {}
    for number, name in pairs:
        if number is not None and number not in seen:
            seen[number] = (name or "").strip()
    return [{"number": n, "name": seen[n]} for n in sorted(seen)]


def tender_exists(session, tender_no: str) -> bool:
    return session.get(Tender, tender_no) is not None


def list_tenders(session):
    return session.execute(select(Tender).order_by(Tender.loaded_at.desc())).scalars().all()


def get_tender_dict(session, tender_no: str):
    """Возвращает тендер в формате, ожидаемом ui_components.render_tender_editor."""
    tender = _get_tender_full(session, tender_no)
    if not tender:
        return None
    return {
        "tender_no": tender.tender_no,
        "title": tender.title or "",
        "customer_name": tender.customer_name or "",
        "customer_address": tender.customer_address or "",
        "protocol_date": tender.protocol_date.strftime("%d.%m.%Y") if tender.protocol_date else "",
        "protocol_time": tender.protocol_datetime.strftime("%H:%M") if tender.protocol_datetime else "",
        "lots": [
            {
                "name": l.name,
                "category": l.category or "",
                "is_failed": bool(l.is_failed),
                "quantity": l.quantity,
                "unit_price": l.unit_price,
                "allocated_amount": l.allocated_amount,
            }
            for l in tender.lots
        ],
        "commission_members": [
            {"full_name": c.full_name, "position": c.position, "role": c.role}
            for c in tender.commission_members
        ],
        "bids": [
            {
                "name": b.supplier.name if b.supplier else "",
                "bin": b.supplier_bin,
                "status": b.status,
                "submitted_at": b.submitted_at.strftime("%d.%m.%Y %H:%M") if b.submitted_at else "",
                "offered_price": b.offered_price,
                "is_winner": b.is_winner,
                "is_second_place": b.is_second_place,
                "rejection_reason": b.rejection_reason or "",
                "criteria": [
                    {"name": c.criterion_name, "value": c.criterion_value, "score": c.criterion_score}
                    for c in b.criteria
                ],
            }
            for b in tender.bids
        ],
    }


def tender_snapshot(tender: Tender) -> dict:
    """Плоский снимок тендера для журнала изменений (old_data/new_data)."""
    return {
        "tender_no": tender.tender_no,
        "title": tender.title,
        "customer_name": tender.customer_name,
        "customer_address": tender.customer_address,
        "protocol_date": _ser(tender.protocol_date),
        "protocol_datetime": _ser(tender.protocol_datetime),
        "lots": [
            {"name": l.name, "category": l.category, "is_failed": bool(l.is_failed),
             "quantity": _ser(l.quantity),
             "unit_price": _ser(l.unit_price), "allocated_amount": _ser(l.allocated_amount)}
            for l in tender.lots
        ],
        "commission_members": [
            {"full_name": c.full_name, "position": c.position, "role": c.role}
            for c in tender.commission_members
        ],
        "bids": [
            {
                "bin": b.supplier_bin, "status": b.status, "is_winner": b.is_winner,
                "is_second_place": b.is_second_place, "offered_price": _ser(b.offered_price),
                "rejection_reason": b.rejection_reason,
                "criteria": [
                    {"name": c.criterion_name, "value": c.criterion_value, "score": _ser(c.criterion_score)}
                    for c in b.criteria
                ],
            }
            for b in tender.bids
        ],
    }


# ---------- запись ----------

def delete_tender(session, tender_no: str, username: str) -> str:
    """
    Полностью удаляет тендер и все связанные записи (лоты, комиссию, заявки,
    критерии) из БД. Пишет запись в журнал изменений перед удалением.
    """
    tender = _get_tender_full(session, tender_no)
    if not tender:
        raise ValueError(f"Тендер №{tender_no} не найден в базе")
    snapshot = tender_snapshot(tender)
    log_change(
        session,
        "tenders",
        tender_no,
        "DELETE",
        snapshot,
        None,
        username,
        "manual_delete",
    )
    session.delete(tender)
    session.commit()
    return tender_no


def log_change(session, table_name, record_id, action, old_data, new_data, username, source):
    session.add(
        AuditLog(
            table_name=table_name,
            record_id=str(record_id),
            action=action,
            old_data=old_data,
            new_data=new_data,
            changed_by=username,
            source=source,
        )
    )


def save_tender(session, data: dict, source: str, username: str,
                 source_file: str = None, file_hash: str = None, overwrite: bool = False,
                 old_tender_no: str = None) -> str:
    """
    Создаёт новый тендер или (если overwrite=True) полностью пересобирает
    существующий вместе со всеми дочерними записями.

    Если передан old_tender_no и он отличается от data["tender_no"] —
    это переименование (правка номера конкурса, который парсер иногда
    распознаёт неверно). Номер конкурса — первичный ключ, на который
    lots/commission_members/bids ссылаются внешним ключом БЕЗ ON UPDATE
    CASCADE, поэтому переименование реализовано как удаление старой
    записи со всеми дочерними (каскадно) и создание новой под новым
    номером с тем же содержимым формы — простое присваивание нового
    значения PK было бы отклонено базой как нарушение ссылочной
    целостности.

    Пишет запись в журнал изменений. Коммитит транзакцию сам; при ошибке —
    откатывает вызывающая сторона (session.rollback()).
    """
    tender_no = (data.get("tender_no") or "").strip()
    if not tender_no:
        raise ValueError("Номер конкурса не может быть пустым")

    renaming = bool(old_tender_no) and old_tender_no != tender_no
    existing = _get_tender_full(session, old_tender_no if renaming else tender_no)

    if renaming:
        if existing is None:
            raise ValueError(f"Тендер №{old_tender_no} не найден в базе")
        if session.get(Tender, tender_no) is not None:
            raise ValueError(f"Тендер №{tender_no} уже существует в базе — выберите другой номер")
    elif existing and not overwrite:
        raise ValueError(f"Тендер {tender_no} уже существует в базе")

    protocol_dt = _parse_datetime_combo(data.get("protocol_date"), data.get("protocol_time"))
    protocol_date_val = _parse_date(data.get("protocol_date"))

    old_snapshot = tender_snapshot(existing) if existing else None

    if renaming:
        # Сохраняем метаданные исходного PDF, раз их не передал вызывающий код.
        source_file = source_file or existing.source_file
        file_hash = file_hash or existing.file_hash
        session.delete(existing)
        session.flush()
        existing = None

    # Находим/создаём заказчика по названию из ЭТОГО протокола — если он уже
    # есть в справочнике, его region_code/region_name не трогаем (только
    # find-or-create, никогда не перезаписываем регион при перезагрузке).
    customer = get_or_create_customer(session, data.get("customer_name"))
    customer_id = customer.id if customer else None

    if existing:
        existing.title = data.get("title")
        existing.customer_name = data.get("customer_name")
        existing.customer_address = data.get("customer_address")
        existing.customer_id = customer_id
        existing.protocol_date = protocol_date_val
        existing.protocol_datetime = protocol_dt
        # Один DELETE на таблицу вместо построчного session.delete() в
        # цикле — не нужно тянуть уже загруженные строки заново, и это один
        # запрос вместо N. bid_criteria удалятся каскадно на уровне БД
        # (ondelete="CASCADE" в модели).
        session.execute(delete(Lot).where(Lot.tender_no == tender_no))
        session.execute(delete(CommissionMember).where(CommissionMember.tender_no == tender_no))
        session.execute(delete(Bid).where(Bid.tender_no == tender_no))
        session.expire(existing, ["lots", "commission_members", "bids"])
        tender = existing
        action = "UPDATE"
    else:
        tender = Tender(
            tender_no=tender_no,
            title=data.get("title"),
            customer_name=data.get("customer_name"),
            customer_address=data.get("customer_address"),
            customer_id=customer_id,
            protocol_date=protocol_date_val,
            protocol_datetime=protocol_dt,
            source_file=source_file,
            file_hash=file_hash,
        )
        session.add(tender)
        action = "UPDATE" if renaming else "INSERT"

    for lot in data.get("lots", []):
        if not (lot.get("name") or "").strip():
            continue
        session.add(
            Lot(
                tender_no=tender_no,
                name=lot.get("name"),
                category=(lot.get("category") or "").strip() or None,
                is_failed=bool(lot.get("is_failed")),
                quantity=_to_float(lot.get("quantity")),
                unit_price=_to_float(lot.get("unit_price")),
                allocated_amount=_to_float(lot.get("allocated_amount")),
            )
        )

    for cm in data.get("commission_members", []):
        if not (cm.get("full_name") or "").strip():
            continue
        session.add(
            CommissionMember(
                tender_no=tender_no,
                full_name=cm.get("full_name"),
                position=cm.get("position"),
                role=cm.get("role"),
            )
        )

    bids_in = [b for b in data.get("bids", []) if _s(b.get("bin")).strip()]

    # Поставщиков подтягиваем одним запросом на все БИН заявок сразу,
    # вместо SELECT-а на каждую заявку по отдельности.
    bins = {_s(b.get("bin")).strip() for b in bids_in}
    suppliers_by_bin = {
        s.bin: s for s in session.execute(select(Supplier).where(Supplier.bin.in_(bins))).scalars()
    } if bins else {}

    pending_bids = []  # (Bid, [критерии]) — критерии добавим после общего flush, когда появятся id
    for b in bids_in:
        bin_ = _s(b.get("bin")).strip()
        name_ = _s(b.get("name")).strip() or None

        supplier = suppliers_by_bin.get(bin_)
        if supplier is None:
            supplier = Supplier(bin=bin_, name=name_)
            session.add(supplier)
            suppliers_by_bin[bin_] = supplier
        elif name_:
            supplier.name = name_

        bid = Bid(
            tender_no=tender_no,
            supplier_bin=bin_,
            submitted_at=_parse_datetime_str(b.get("submitted_at")),
            status=_s(b.get("status")).strip() or None,
            rejection_reason=_s(b.get("rejection_reason")).strip() or None,
            is_winner=bool(b.get("is_winner")),
            is_second_place=bool(b.get("is_second_place")),
            offered_price=_to_float(b.get("offered_price")),
        )
        session.add(bid)
        pending_bids.append((bid, b.get("criteria") or []))

    # Один flush на все заявки разом (а не по одному на заявку) — нужен
    # только затем, чтобы у каждой bid появился id перед вставкой критериев.
    session.flush()

    for bid, criteria in pending_bids:
        for crit in criteria:
            name = _s(crit.get("name")).strip()
            if not name:
                continue
            session.add(
                BidCriterion(
                    bid_id=bid.id,
                    criterion_name=name,
                    criterion_value=(_s(crit.get("value")).strip() or None),
                    criterion_score=_to_float(crit.get("score")),
                )
            )

    session.flush()

    # new_data журнала собираем прямо из уже провалидированного data, а не
    # перечитыванием только что записанных связей — быстрее (без лишних
    # запросов) и надёжнее, т.к. relationship-коллекции tender не
    # синхронизируются автоматически при массовом DELETE/точечных INSERT
    # в обход них.
    new_snapshot = {
        "tender_no": tender_no,
        "title": tender.title,
        "customer_name": tender.customer_name,
        "customer_address": tender.customer_address,
        "protocol_date": _ser(protocol_date_val),
        "protocol_datetime": _ser(protocol_dt),
        "lots": [
            {
                "name": lot.get("name"),
                "category": (lot.get("category") or "").strip() or None,
                "is_failed": bool(lot.get("is_failed")),
                "quantity": _to_float(lot.get("quantity")),
                "unit_price": _to_float(lot.get("unit_price")),
                "allocated_amount": _to_float(lot.get("allocated_amount")),
            }
            for lot in data.get("lots", []) if (lot.get("name") or "").strip()
        ],
        "commission_members": [
            {"full_name": cm.get("full_name"), "position": cm.get("position"), "role": cm.get("role")}
            for cm in data.get("commission_members", []) if (cm.get("full_name") or "").strip()
        ],
        "bids": [
            {
                "bin": _s(b.get("bin")).strip(),
                "status": _s(b.get("status")).strip() or None,
                "is_winner": bool(b.get("is_winner")),
                "is_second_place": bool(b.get("is_second_place")),
                "offered_price": _to_float(b.get("offered_price")),
                "rejection_reason": _s(b.get("rejection_reason")).strip() or None,
                "criteria": [
                    {
                        "name": _s(c.get("name")).strip(),
                        "value": _s(c.get("value")).strip() or None,
                        "score": _to_float(c.get("score")),
                    }
                    for c in (b.get("criteria") or []) if _s(c.get("name")).strip()
                ],
            }
            for b in bids_in
        ],
    }
    log_change(session, "tenders", tender_no, action, old_snapshot, new_snapshot, username, source)
    session.commit()
    return tender_no


def save_supplier_region(session, bin_: str, region_number: int, region_name: str, username: str):
    """Задаёт регион поставщику (вкладка "Региональность")."""
    supplier = session.get(Supplier, bin_)
    if not supplier:
        raise ValueError(f"Поставщик с БИН {bin_} не найден в базе")
    old = {"region_number": supplier.region_number, "region_name": supplier.region_name}
    supplier.region_number = region_number
    supplier.region_name = region_name
    log_change(
        session, "suppliers", bin_, "UPDATE", old,
        {"region_number": region_number, "region_name": region_name},
        username, "region_update",
    )
    session.commit()


def save_customer_region(session, customer_id: int, region_number: int, region_name: str, username: str):
    """Задаёт регион заказчику (вкладка "Региональность") — теперь это
    просто одна строка в customers, а не рассылка по всем его тендерам."""
    customer = session.get(Customer, customer_id)
    if not customer:
        raise ValueError(f"Заказчик с id={customer_id} не найден в базе")
    old = {"region_code": customer.region_code, "region_name": customer.region_name}
    customer.region_code = region_number
    customer.region_name = region_name
    log_change(
        session, "customers", str(customer_id), "UPDATE", old,
        {"region_code": region_number, "region_name": region_name},
        username, "region_update",
    )
    session.commit()


def log_scrape_download(session, run_id: str, tender_no: str, concurs_name: str,
                         total_sum, filename: str = None, status: str = "success",
                         reason: str = None, scan_type: str = "completed"):
    """Записывает один результат сессии парсинга (разделы "Парсинг данных" и
    "Парсинг несостоявшихся") — как успешно скачанный протокол
    (status="success", filename задан), так и неудачную попытку
    (status="failed", reason — причина). scan_type разделяет запуски двух
    разделов между собой. Коммит делает вызывающая сторона (по одному разу
    на всю сессию, а не на каждую запись)."""
    session.add(
        ScrapeDownload(
            run_id=run_id,
            scan_type=scan_type,
            tender_no=tender_no,
            concurs_name=concurs_name,
            total_sum=total_sum,
            filename=filename,
            status=status,
            reason=reason,
        )
    )


def get_last_scrape_session(session, scan_type: str = "completed"):
    """Все строки последней сессии скачивания нужного раздела (scan_type),
    или пустой список, если такой парсинг ещё не запускали.

    Строки, записанные до появления scan_type, считаются относящимися к
    завершённым конкурсам — тогда другого раздела просто не существовало."""
    type_filter = ScrapeDownload.scan_type == scan_type
    if scan_type == "completed":
        type_filter = or_(type_filter, ScrapeDownload.scan_type.is_(None))

    last_run = session.execute(
        select(ScrapeDownload.run_id)
        .where(type_filter)
        .order_by(ScrapeDownload.downloaded_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if not last_run:
        return []
    return session.execute(
        select(ScrapeDownload)
        .where(ScrapeDownload.run_id == last_run)
        .order_by(ScrapeDownload.downloaded_at)
    ).scalars().all()


# ---------- ИИ ассистент ----------

def get_supplier_ai_profile(session, supplier_bin: str):
    """
    Профиль подрядчика для прогноза раздела "ИИ ассистент": по каждой его
    заявке — конкурс, категория работ, сумма конкурса, число участников,
    результат и баллы по каждому критерию оценки. Это и есть исходные
    данные, на основании которых модель прогнозирует баллы по будущему
    конкурсу — без них прогноз был бы обычной догадкой.
    """
    supplier = session.get(Supplier, supplier_bin)
    if not supplier:
        return None

    bids = session.execute(
        select(Bid)
        .where(Bid.supplier_bin == supplier_bin)
        .options(selectinload(Bid.criteria), selectinload(Bid.tender).selectinload(Tender.lots))
        .order_by(Bid.submitted_at.desc().nullslast())
    ).scalars().all()

    tender_nos = [b.tender_no for b in bids]
    participants_by_tender = dict(
        session.execute(
            select(Bid.tender_no, func.count(Bid.id))
            .where(Bid.tender_no.in_(tender_nos))
            .group_by(Bid.tender_no)
        ).all()
    ) if tender_nos else {}

    history = []
    for b in bids:
        tender = b.tender
        categories = sorted(
            {(l.category or "").strip() for l in tender.lots if (l.category or "").strip()}
        ) if tender else []
        total_sum = sum((l.allocated_amount or 0) for l in tender.lots) if tender else None
        history.append({
            "tender_no": b.tender_no,
            "title": tender.title if tender else None,
            "categories": categories,
            "total_sum": float(total_sum) if total_sum else None,
            "participants_count": participants_by_tender.get(b.tender_no, 1),
            "status": b.status,
            "is_winner": b.is_winner,
            "is_second_place": b.is_second_place,
            "rejection_reason": b.rejection_reason,
            "criteria": [
                {
                    "name": c.criterion_name,
                    "value": c.criterion_value,
                    "score": float(c.criterion_score) if c.criterion_score is not None else None,
                }
                for c in b.criteria
            ],
        })

    return {
        "bin": supplier.bin,
        "name": supplier.name or supplier.bin,
        "region_number": supplier.region_number,
        "region_name": supplier.region_name,
        "history": history,
    }


def list_suppliers_in_region(session, region_number: int, exclude_bin: str = None):
    """Подрядчики того же региона — конкурентная среда будущего конкурса,
    с их суммарной статистикой участий/побед по всей истории в базе."""
    stmt = select(Supplier).where(Supplier.region_number == region_number)
    if exclude_bin:
        stmt = stmt.where(Supplier.bin != exclude_bin)
    suppliers = session.execute(stmt.order_by(Supplier.name)).scalars().all()

    bins = [s.bin for s in suppliers]
    stats = {}
    if bins:
        rows = session.execute(
            select(
                Bid.supplier_bin,
                func.count(Bid.id),
                func.sum(case((Bid.is_winner.is_(True), 1), else_=0)),
            ).where(Bid.supplier_bin.in_(bins)).group_by(Bid.supplier_bin)
        ).all()
        stats = {bin_: {"bids": cnt, "wins": wins or 0} for bin_, cnt, wins in rows}

    return [
        {
            "bin": s.bin,
            "name": s.name or s.bin,
            "bids_count": stats.get(s.bin, {}).get("bids", 0),
            "wins_count": stats.get(s.bin, {}).get("wins", 0),
        }
        for s in suppliers
    ]


def save_ai_report(session, supplier_bin, supplier_name, region_number, region_name,
                    future_sum, report_text, username) -> int:
    report = AIReport(
        supplier_bin=supplier_bin,
        supplier_name=supplier_name,
        region_number=region_number,
        region_name=region_name,
        future_sum=future_sum,
        report_text=report_text,
        created_by=username,
    )
    session.add(report)
    session.commit()
    return report.id


def list_ai_reports(session, limit: int = 30):
    """Последние сгенерированные отчёты — самые новые первыми."""
    return session.execute(
        select(AIReport).order_by(AIReport.created_at.desc()).limit(limit)
    ).scalars().all()
