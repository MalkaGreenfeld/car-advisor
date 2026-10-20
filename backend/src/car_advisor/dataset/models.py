from datetime import date

from sqlalchemy import Index, String, Unicode
from sqlalchemy.orm import Mapped, mapped_column

from car_advisor.db import Base


class UsageValueRow(Base):
    __tablename__ = "usage_values"
    __table_args__ = (
        Index("ix_usage_values_tax_year_registration_year", "tax_year", "registration_year"),
    )

    tax_year: Mapped[int] = mapped_column(primary_key=True)
    registration_year: Mapped[int] = mapped_column(primary_key=True)
    # The codes are text so that leading zeros such as 01229 survive (Req 1.3).
    vehicle_type_code: Mapped[str] = mapped_column(String(10), primary_key=True)
    manufacturer_code: Mapped[str] = mapped_column(String(10), primary_key=True)
    model_code: Mapped[str] = mapped_column(String(10), primary_key=True)
    manufacturer_name: Mapped[str] = mapped_column(Unicode(100))
    model_description: Mapped[str] = mapped_column(Unicode(100))
    fuel_type: Mapped[str] = mapped_column(String(10))
    trade_name: Mapped[str] = mapped_column(Unicode(100))
    automatic: Mapped[int]
    electric_raw: Mapped[int]
    green_score: Mapped[int]
    pollution_level: Mapped[int]
    engine_cc: Mapped[int]
    weight: Mapped[int]
    effective_date: Mapped[date]
    list_price: Mapped[int]
    adjusted_price: Mapped[int]
    usage_value: Mapped[int]
    powertrain: Mapped[str] = mapped_column(String(12))
