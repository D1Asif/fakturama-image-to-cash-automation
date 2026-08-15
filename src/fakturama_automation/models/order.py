from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel


class Address(BaseModel):
    street: str
    zip: str
    city: str
    country: str
    email: str | None = None
    telephone: str | None = None


class DebtorData(BaseModel):
    company: str
    first_name: str
    last_name: str
    alias: str | None = None
    billing_address: Address
    delivery_address: Address


class OrderItem(BaseModel):
    sku: str
    description: str
    quantity: Decimal
    unit_net_price: Decimal
    vat_percentage: Decimal
    discount_percentage: Decimal = Decimal("0")
    source_total: Decimal | None = None


class PaymentData(BaseModel):
    method: str
    status: Literal["PAID", "UNPAID"]
    payment_date: date | None = None


class OrderData(BaseModel):
    order_date: date
    external_reference: str

    debtor: DebtorData
    payment: PaymentData

    items: list[OrderItem]

    total_net: Decimal
    vat_total: Decimal
    total: Decimal
