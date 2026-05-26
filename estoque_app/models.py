from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from database import Base


def now_utc():
    return datetime.utcnow()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(80), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, default="OPERADOR")
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=now_utc)

    movements = relationship("Movement", back_populates="usuario")


class SKU(Base):
    __tablename__ = "skus"

    id = Column(Integer, primary_key=True)
    sku = Column(String(80), unique=True, nullable=False, index=True)
    descricao = Column(String(255), nullable=False)
    unidade = Column(String(20), nullable=True)
    categoria = Column(String(120), nullable=True)
    localizacao = Column(String(120), nullable=True)
    estoque_minimo = Column(Numeric(14, 3), nullable=False, default=0)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=now_utc)
    updated_at = Column(DateTime, nullable=False, default=now_utc, onupdate=now_utc)

    balance = relationship("StockBalance", back_populates="sku", uselist=False)
    movements = relationship("Movement", back_populates="sku")


class StockBalance(Base):
    __tablename__ = "stock_balances"

    id = Column(Integer, primary_key=True)
    sku_id = Column(Integer, ForeignKey("skus.id"), unique=True, nullable=False, index=True)
    saldo_atual = Column(Numeric(14, 3), nullable=False, default=0)
    updated_at = Column(DateTime, nullable=False, default=now_utc, onupdate=now_utc)

    sku = relationship("SKU", back_populates="balance")


class Movement(Base):
    __tablename__ = "movements"

    id = Column(Integer, primary_key=True)
    sku_id = Column(Integer, ForeignKey("skus.id"), nullable=False, index=True)
    tipo = Column(String(20), nullable=False, index=True)
    quantidade = Column(Numeric(14, 3), nullable=False)
    saldo_anterior = Column(Numeric(14, 3), nullable=False)
    saldo_posterior = Column(Numeric(14, 3), nullable=False)
    usuario_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    documento = Column(String(120), nullable=True)
    observacao = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=now_utc, index=True)

    sku = relationship("SKU", back_populates="movements")
    usuario = relationship("User", back_populates="movements")


class InventorySession(Base):
    __tablename__ = "inventory_sessions"

    id = Column(Integer, primary_key=True)
    status = Column(String(20), nullable=False, default="ABERTO", index=True)
    opened_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    closed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    opened_at = Column(DateTime, nullable=False, default=now_utc)
    closed_at = Column(DateTime, nullable=True)
    observacao = Column(Text, nullable=True)

    counts = relationship("InventoryCount", back_populates="session")


class InventoryCount(Base):
    __tablename__ = "inventory_counts"
    __table_args__ = (UniqueConstraint("session_id", "sku_id", name="uq_inventory_count_sku"),)

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("inventory_sessions.id"), nullable=False, index=True)
    sku_id = Column(Integer, ForeignKey("skus.id"), nullable=False, index=True)
    quantidade_contada = Column(Numeric(14, 3), nullable=False)
    saldo_sistema = Column(Numeric(14, 3), nullable=False)
    diferenca = Column(Numeric(14, 3), nullable=False)
    counted_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    counted_at = Column(DateTime, nullable=False, default=now_utc)

    session = relationship("InventorySession", back_populates="counts")
    sku = relationship("SKU")
    user = relationship("User")


class LabelPrintJob(Base):
    __tablename__ = "label_print_jobs"

    id = Column(Integer, primary_key=True)
    sku_id = Column(Integer, ForeignKey("skus.id"), nullable=False, index=True)
    quantidade = Column(Integer, nullable=False, default=1)
    status = Column(String(20), nullable=False, default="PENDENTE", index=True)
    origem = Column(String(20), nullable=False, default="MANUAL", index=True)
    usuario_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    inventory_session_id = Column(Integer, ForeignKey("inventory_sessions.id"), nullable=True)
    zpl_path = Column(String(500), nullable=True)
    erro = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=now_utc, index=True)
    printed_at = Column(DateTime, nullable=True)

    sku = relationship("SKU")
    usuario = relationship("User")
    inventory_session = relationship("InventorySession")


class AppSetting(Base):
    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True)
    key = Column(String(80), unique=True, nullable=False, index=True)
    value = Column(String(255), nullable=False)
    updated_at = Column(DateTime, nullable=False, default=now_utc, onupdate=now_utc)
