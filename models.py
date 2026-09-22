"""
Модели базы данных (SQLAlchemy ORM).

Схема специально нормализована и использует "длинный" формат для критериев
оценки (bid_criteria), а не отдельную колонку под каждый критерий — состав
критериев оценки отличается от закупки к закупке, и такой подход позволяет
новым критериям появляться в PDF без изменения схемы БД и без поломки
дашбордов в Power BI.
"""

from sqlalchemy import (
    Column, Integer, String, Text, Numeric, Boolean, Date, DateTime,
    ForeignKey, JSON, UniqueConstraint, func
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Tender(Base):
    """Один протокол результатов конкурса = одна закупка."""
    __tablename__ = "tenders"

    tender_no = Column(String, primary_key=True)          # напр. "26000514KR-1"
    title = Column(Text)                                    # название конкурса
    customer_name = Column(Text)                             # как написано в ЭТОМ протоколе
    customer_address = Column(Text)
    protocol_date = Column(Date)
    protocol_datetime = Column(DateTime)
    # Конкурс признан несостоявшимся — признак всего конкурса, а не
    # отдельной позиции закупки. Проставляется автоматически при разборе
    # протокола: parser находит в нём фразу "Признать закупку …
    # несостоявшейся" (см. parser._parse_failed_status).
    is_failed = Column(Boolean, default=False, index=True)
    source_file = Column(Text)                              # имя загруженного PDF
    file_hash = Column(String)                               # sha256 файла (информационно)
    loaded_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Справочник заказчика (регион и т.п. живут в Customer, не здесь —
    # customer_name выше остаётся как есть, это исходный текст из протокола).
    customer_id = Column(Integer, ForeignKey("customers.id"), index=True)

    customer = relationship("Customer", back_populates="tenders")
    lots = relationship("Lot", back_populates="tender", cascade="all, delete-orphan")
    commission_members = relationship("CommissionMember", back_populates="tender", cascade="all, delete-orphan")
    bids = relationship("Bid", back_populates="tender", cascade="all, delete-orphan")


class Customer(Base):
    """Справочник заказчиков — не дублируется между тендерами, ключ по
    точному названию (см. crud.get_or_create_customer)."""
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True)
    name = Column(Text, nullable=False, unique=True)
    region_code = Column(Integer)
    region_name = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    tenders = relationship("Tender", back_populates="customer")


class Lot(Base):
    """Строка из таблицы 'Перечень закупаемых работ, услуг'."""
    __tablename__ = "lots"

    id = Column(Integer, primary_key=True)
    tender_no = Column(String, ForeignKey("tenders.tender_no", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(Text)
    category = Column(Text)
    quantity = Column(Numeric)
    unit_price = Column(Numeric)
    allocated_amount = Column(Numeric)

    tender = relationship("Tender", back_populates="lots")


class CommissionMember(Base):
    """Член конкурсной комиссии."""
    __tablename__ = "commission_members"

    id = Column(Integer, primary_key=True)
    tender_no = Column(String, ForeignKey("tenders.tender_no", ondelete="CASCADE"), nullable=False, index=True)
    full_name = Column(Text)
    position = Column(Text)
    role = Column(Text)

    tender = relationship("Tender", back_populates="commission_members")


class Supplier(Base):
    """Справочник поставщиков — не дублируется между тендерами, ключ БИН."""
    __tablename__ = "suppliers"

    bin = Column(String, primary_key=True)
    name = Column(Text)

    # Регион подрядчика (вкладка "Региональность").
    region_number = Column(Integer)
    region_name = Column(Text)

    bids = relationship("Bid", back_populates="supplier")


class Bid(Base):
    """Заявка конкретного поставщика на конкретный тендер."""
    __tablename__ = "bids"

    id = Column(Integer, primary_key=True)
    tender_no = Column(String, ForeignKey("tenders.tender_no", ondelete="CASCADE"), nullable=False, index=True)
    supplier_bin = Column(String, ForeignKey("suppliers.bin"), nullable=False, index=True)

    submitted_at = Column(DateTime)
    status = Column(String)                # "Допущен" / "Отклонён"
    rejection_reason = Column(Text)
    is_winner = Column(Boolean, default=False)
    is_second_place = Column(Boolean, default=False)
    offered_price = Column(Numeric)        # факт: в протоколе почти всегда пусто (публикуется только балл)

    tender = relationship("Tender", back_populates="bids")
    supplier = relationship("Supplier", back_populates="bids")
    criteria = relationship("BidCriterion", back_populates="bid", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("tender_no", "supplier_bin", name="uq_bid_tender_supplier"),
    )


class BidCriterion(Base):
    """
    Один критерий оценки одной заявки — 'длинный' формат вместо колонки на
    каждый критерий. Например: ('Гарантия качества на оборудование', '160', None)
    или ('Отказ от аванса', 'да', 3.0).
    """
    __tablename__ = "bid_criteria"

    id = Column(Integer, primary_key=True)
    bid_id = Column(Integer, ForeignKey("bids.id", ondelete="CASCADE"), nullable=False, index=True)
    criterion_name = Column(Text, nullable=False)
    criterion_value = Column(Text)
    criterion_score = Column(Numeric)

    bid = relationship("Bid", back_populates="criteria")


class AuditLog(Base):
    """Журнал изменений — кто, когда и что поменял через приложение."""
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True)
    table_name = Column(String)
    record_id = Column(String, index=True)  # tender_no затронутой записи
    action = Column(String)                # INSERT / UPDATE / DELETE
    old_data = Column(JSON)
    new_data = Column(JSON)
    changed_by = Column(String)
    source = Column(String)                # pdf_upload / manual_entry / manual_edit / manual_delete
    changed_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class ScrapeDownload(Base):
    """
    История протоколов, скачанных через раздел "Парсинг данных" с портала
    meks.zakup.sk.kz. Отдельно от audit_log — здесь только результаты
    сессий скачивания (файл ещё не разобран и не сохранён в тендеры,
    это отдельный шаг через страницу "Загрузка PDF").
    """
    __tablename__ = "scrape_downloads"

    id = Column(Integer, primary_key=True)
    run_id = Column(String, index=True)     # общий для всех строк одного запуска
    # Какой раздел запускал обход: "completed" (завершённые конкурсы) или
    # "failed" (несостоявшиеся). Без этого признака страницы показывали бы
    # друг другу чужую "последнюю сессию" — таблица-то общая.
    scan_type = Column(String, index=True, default="completed")
    tender_no = Column(String, index=True)
    concurs_name = Column(Text)
    total_sum = Column(Numeric)
    filename = Column(Text)
    status = Column(String, default="success")   # success / failed
    reason = Column(Text)                          # причина, если status="failed"
    downloaded_at = Column(DateTime(timezone=True), server_default=func.now())


class AIReport(Base):
    """
    История отчётов раздела "ИИ ассистент" — какой подрядчик/регион/сумма
    запрашивались и полный текст ответа модели (DeepSeek), чтобы прогнозы
    можно было пересмотреть позже, а не только в момент генерации.
    """
    __tablename__ = "ai_reports"

    id = Column(Integer, primary_key=True)
    supplier_bin = Column(String, ForeignKey("suppliers.bin"), index=True)
    supplier_name = Column(Text)
    region_number = Column(Integer)
    region_name = Column(Text)
    future_sum = Column(Numeric)
    report_text = Column(Text)
    created_by = Column(String)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
