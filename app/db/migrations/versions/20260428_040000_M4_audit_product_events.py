"""M4: таблицы audit_event (append-only) + product_event (partitioned by month).

Revision ID: 20260428_040000_m4_audit_product_events
Revises: 20260428_030000_m3_user_session_magic_link
Create Date: 2026-04-28

- audit_event: BigSerial PK, append-only триггер, ip INET, details JSONB
- product_event: RANGE PARTITION BY ts, 7 партиций вперёд (текущий месяц + 6)
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260428_040000_m4_audit"
down_revision: Union[str, None] = "20260428_030000_m3_sessions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_event",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("ip", postgresql.INET, nullable=True),
        sa.Column("user_agent", sa.Text, nullable=True),
        sa.Column("details", postgresql.JSONB, nullable=True),
    )
    op.create_index(
        "idx_audit_event_user_ts",
        "audit_event",
        ["user_id", sa.text("ts DESC")],
    )
    op.create_index(
        "idx_audit_event_type_ts",
        "audit_event",
        ["event_type", sa.text("ts DESC")],
    )

    # Триггер: запрет UPDATE/DELETE (audit_event — append-only)
    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_event_immutable() RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'audit_event is append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_event_no_modify
            BEFORE UPDATE OR DELETE ON audit_event
            FOR EACH ROW EXECUTE FUNCTION audit_event_immutable();
        """
    )

    # product_event с monthly partitioning
    op.execute(
        """
        CREATE TABLE product_event (
            id BIGSERIAL,
            user_id INTEGER,
            event_type VARCHAR(64) NOT NULL,
            ts TIMESTAMPTZ NOT NULL DEFAULT now(),
            properties JSONB,
            PRIMARY KEY (id, ts)
        ) PARTITION BY RANGE (ts);
        """
    )
    op.execute(
        """
        DO $$
        DECLARE
            start_dt DATE := date_trunc('month', now())::DATE;
            i INT;
            partition_name TEXT;
            from_dt DATE;
            to_dt DATE;
        BEGIN
            FOR i IN 0..6 LOOP
                from_dt := (start_dt + (i || ' month')::INTERVAL)::DATE;
                to_dt   := (start_dt + ((i + 1) || ' month')::INTERVAL)::DATE;
                partition_name := 'product_event_' || to_char(from_dt, 'YYYY_MM');
                EXECUTE format(
                    'CREATE TABLE IF NOT EXISTS %I PARTITION OF product_event '
                    'FOR VALUES FROM (%L) TO (%L);',
                    partition_name, from_dt, to_dt
                );
            END LOOP;
        END $$;
        """
    )
    op.create_index(
        "idx_product_event_user_ts",
        "product_event",
        ["user_id", sa.text("ts DESC")],
    )
    op.create_index(
        "idx_product_event_type_ts",
        "product_event",
        ["event_type", sa.text("ts DESC")],
    )


def downgrade() -> None:
    op.drop_index("idx_product_event_type_ts", table_name="product_event")
    op.drop_index("idx_product_event_user_ts", table_name="product_event")
    op.execute("DROP TABLE IF EXISTS product_event CASCADE;")
    op.execute("DROP TRIGGER IF EXISTS audit_event_no_modify ON audit_event;")
    op.execute("DROP FUNCTION IF EXISTS audit_event_immutable();")
    op.drop_index("idx_audit_event_type_ts", table_name="audit_event")
    op.drop_index("idx_audit_event_user_ts", table_name="audit_event")
    op.drop_table("audit_event")
