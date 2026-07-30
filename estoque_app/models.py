from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
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
    auth_version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, nullable=False, default=now_utc)

    movements = relationship(
        "Movement",
        foreign_keys="Movement.usuario_id",
        back_populates="usuario",
    )


class ErpRole(Base):
    __tablename__ = "erp_roles"

    code = Column(String(40), primary_key=True)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=False, default="")
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=now_utc)
    updated_at = Column(DateTime, nullable=False, default=now_utc, onupdate=now_utc)


class ErpPermission(Base):
    __tablename__ = "erp_permissions"

    code = Column(String(120), primary_key=True)
    module = Column(String(40), nullable=False, index=True)
    description = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=now_utc)


class ErpRolePermission(Base):
    __tablename__ = "erp_role_permissions"

    role_code = Column(
        String(40),
        ForeignKey(
            "erp_roles.code",
            onupdate="CASCADE",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    permission_code = Column(
        String(120),
        ForeignKey("erp_permissions.code", ondelete="CASCADE"),
        primary_key=True,
    )


class ErpUserRole(Base):
    __tablename__ = "erp_user_roles"

    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role_code = Column(
        String(40),
        ForeignKey(
            "erp_roles.code",
            onupdate="CASCADE",
            ondelete="RESTRICT",
        ),
        primary_key=True,
    )
    assigned_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    assigned_at = Column(DateTime, nullable=False, default=now_utc)


class ErpUserPermissionOverride(Base):
    __tablename__ = "erp_user_permission_overrides"

    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    permission_code = Column(
        String(120),
        ForeignKey("erp_permissions.code", ondelete="CASCADE"),
        primary_key=True,
    )
    allowed = Column(Boolean, nullable=False)
    reason = Column(Text, nullable=False, default="")
    assigned_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    assigned_at = Column(DateTime, nullable=False, default=now_utc)


class ErpAuthAuditEvent(Base):
    __tablename__ = "erp_auth_audit_events"

    id = Column(
        Uuid(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    actor_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    action = Column(String(80), nullable=False)
    before_data = Column(JSON, nullable=False, default=dict)
    after_data = Column(JSON, nullable=False, default=dict)
    reason = Column(Text, nullable=False, default="")
    origin_app = Column(String(40), nullable=False, default="ESTOQUE")
    created_at = Column(DateTime, nullable=False, default=now_utc)


class SKU(Base):
    __tablename__ = "skus"

    id = Column(Integer, primary_key=True)
    sku = Column(String(80), unique=True, nullable=False, index=True)
    descricao = Column(String(255), nullable=False)
    unidade = Column(String(20), nullable=True)
    grupo = Column(String(120), nullable=True)
    categoria = Column(String(120), nullable=True)
    localizacao = Column(String(120), nullable=True)
    estoque_minimo = Column(Numeric(14, 3), nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=now_utc)
    updated_at = Column(DateTime, nullable=False, default=now_utc, onupdate=now_utc)

    balance = relationship("StockBalance", back_populates="sku", uselist=False)
    movements = relationship("Movement", back_populates="sku")
    bom_components = relationship(
        "BomComponent",
        foreign_keys="BomComponent.item_sku_id",
        back_populates="item_sku",
    )


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
    related_movement_id = Column(Integer, ForeignKey("movements.id"), nullable=True, index=True)
    documento = Column(String(120), nullable=True)
    observacao = Column(Text, nullable=True)
    source_type = Column(String(40), nullable=True, index=True)
    source_id = Column(Uuid(as_uuid=False), nullable=True, index=True)
    source_line_id = Column(Uuid(as_uuid=False), nullable=True, index=True)
    idempotency_key = Column(String(160), nullable=True, unique=True, index=True)
    work_order_id = Column(Uuid(as_uuid=False), nullable=True, index=True)
    context_kind = Column(String(20), nullable=True)
    setor = Column(String(120), nullable=True)
    reference_text = Column(String(255), nullable=True)
    link_updated_at = Column(DateTime, nullable=True)
    link_updated_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    movement_status = Column(String(20), nullable=False, default="ATIVA", index=True)
    canceled_at = Column(DateTime, nullable=True)
    canceled_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    cancel_reason = Column(Text, nullable=True)
    reversal_movement_id = Column(Integer, ForeignKey("movements.id"), nullable=True)
    operation_id = Column(Uuid(as_uuid=False), nullable=True, index=True)
    parent_movement_id = Column(
        Integer,
        ForeignKey("movements.id"),
        nullable=True,
        index=True,
    )
    created_at = Column(DateTime, nullable=False, default=now_utc, index=True)

    sku = relationship("SKU", back_populates="movements")
    usuario = relationship("User", foreign_keys=[usuario_id], back_populates="movements")
    related_movement = relationship(
        "Movement",
        foreign_keys=[related_movement_id],
        remote_side=[id],
        backref="related_movements",
    )
    cancellation_reversal = relationship(
        "Movement",
        foreign_keys=[reversal_movement_id],
        remote_side=[id],
        post_update=True,
    )
    parent_movement = relationship(
        "Movement",
        foreign_keys=[parent_movement_id],
        remote_side=[id],
        backref="operation_children",
    )


class ErpMovementReferenceHistory(Base):
    __tablename__ = "erp_movement_reference_history"

    id = Column(Uuid(as_uuid=False), primary_key=True)
    movement_id = Column(
        Integer,
        ForeignKey("movements.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    previous_work_order_id = Column(Uuid(as_uuid=False), nullable=True)
    new_work_order_id = Column(Uuid(as_uuid=False), nullable=True)
    previous_context_kind = Column(String(20), nullable=True)
    new_context_kind = Column(String(20), nullable=True)
    previous_setor = Column(String(120), nullable=True)
    new_setor = Column(String(120), nullable=True)
    previous_reference_text = Column(String(255), nullable=True)
    new_reference_text = Column(String(255), nullable=True)
    changed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    reason = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=now_utc)


class DashboardMovementCache(Base):
    __tablename__ = "dashboard_movement_cache"

    id = Column(Integer, primary_key=True)
    movement_id = Column(Integer, nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, index=True)
    usuario_id = Column(Integer, nullable=True)
    usuario_nome = Column(String(80), nullable=True)
    sku_id = Column(Integer, nullable=True)
    sku_codigo = Column(String(80), nullable=False)
    descricao = Column(String(255), nullable=False)
    tipo = Column(String(20), nullable=False, index=True)
    quantidade = Column(Numeric(14, 3), nullable=False)
    saldo_anterior = Column(Numeric(14, 3), nullable=False)
    saldo_posterior = Column(Numeric(14, 3), nullable=False)
    documento = Column(String(120), nullable=True)
    observacao = Column(Text, nullable=True)
    cached_at = Column(DateTime, nullable=False, default=now_utc)


class BomComponent(Base):
    __tablename__ = "bom_components"
    __table_args__ = (
        UniqueConstraint("item_sku_id", "component_sku_id", name="uq_bom_item_component"),
    )

    id = Column(Integer, primary_key=True)
    item_sku_id = Column(Integer, ForeignKey("skus.id"), nullable=False, index=True)
    component_sku_id = Column(Integer, ForeignKey("skus.id"), nullable=False, index=True)
    descricao = Column(String(255), nullable=True)
    unidade = Column(String(20), nullable=True)
    quantidade = Column(Numeric(14, 3), nullable=False)
    created_at = Column(DateTime, nullable=False, default=now_utc)
    updated_at = Column(DateTime, nullable=False, default=now_utc, onupdate=now_utc)

    item_sku = relationship("SKU", foreign_keys=[item_sku_id], back_populates="bom_components")
    component_sku = relationship("SKU", foreign_keys=[component_sku_id])


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
