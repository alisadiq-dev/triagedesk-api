"""initial schema: profiles, categories, sla_policies, tickets, comments, events

Revision ID: 0001
Revises:
Create Date: 2026-10-02

Reviewed by hand: identity keys, CHECK constraints (enums, resolved_at invariant,
customer/internal rule), partial SLA indexes, generated tsvector column with GIN index.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_categories")),
    )
    op.create_index(
        "uq_categories_name_lower", "categories", [sa.literal_column("lower(name)")], unique=True
    )
    op.create_table(
        "profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column(
            "role",
            sa.Enum(
                "customer",
                "agent",
                "admin",
                name="role",
                native_enum=False,
                length=32,
                create_constraint=True,
            ),
            server_default=sa.text("'customer'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_profiles")),
    )
    op.create_table(
        "sla_policies",
        sa.Column(
            "priority",
            sa.Enum(
                "low",
                "medium",
                "high",
                "urgent",
                name="priority",
                native_enum=False,
                length=32,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("response_hours", sa.Integer(), nullable=False),
        sa.Column("resolution_hours", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "resolution_hours >= response_hours",
            name=op.f("ck_sla_policies_resolution_after_response"),
        ),
        sa.CheckConstraint(
            "response_hours > 0", name=op.f("ck_sla_policies_response_hours_positive")
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["profiles.id"], name=op.f("fk_sla_policies_updated_by_profiles")
        ),
        sa.PrimaryKeyConstraint("priority", name=op.f("pk_sla_policies")),
    )
    op.create_table(
        "tickets",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("customer_id", sa.Uuid(), nullable=False),
        sa.Column("assignee_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=True),
        sa.Column(
            "category_source",
            sa.Enum(
                "ai",
                "human",
                name="category_source",
                native_enum=False,
                length=32,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column(
            "priority",
            sa.Enum(
                "low",
                "medium",
                "high",
                "urgent",
                name="priority",
                native_enum=False,
                length=32,
                create_constraint=True,
            ),
            server_default=sa.text("'medium'"),
            nullable=False,
        ),
        sa.Column(
            "priority_source",
            sa.Enum(
                "default",
                "ai",
                "keyword",
                "human",
                name="priority_source",
                native_enum=False,
                length=32,
                create_constraint=True,
            ),
            server_default=sa.text("'default'"),
            nullable=False,
        ),
        sa.Column(
            "sentiment",
            sa.Enum(
                "positive",
                "neutral",
                "negative",
                name="sentiment",
                native_enum=False,
                length=32,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "open",
                "in_progress",
                "waiting_on_customer",
                "resolved",
                "closed",
                name="status",
                native_enum=False,
                length=32,
                create_constraint=True,
            ),
            server_default=sa.text("'open'"),
            nullable=False,
        ),
        sa.Column(
            "ai_status",
            sa.Enum(
                "pending",
                "completed",
                "failed",
                name="ai_status",
                native_enum=False,
                length=32,
                create_constraint=True,
            ),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("ai_suggested_reply", sa.Text(), nullable=True),
        sa.Column("ai_model", sa.Text(), nullable=True),
        sa.Column("ai_prompt_version", sa.Text(), nullable=True),
        sa.Column("first_response_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolution_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(
                "to_tsvector('english', coalesce(title, '') || ' ' || coalesce(description, ''))",
                persisted=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status IN ('resolved', 'closed')) = (resolved_at IS NOT NULL)",
            name=op.f("ck_tickets_resolved_at_matches_status"),
        ),
        sa.CheckConstraint(
            "length(description) BETWEEN 1 AND 10000", name=op.f("ck_tickets_description_length")
        ),
        sa.ForeignKeyConstraint(
            ["assignee_id"], ["profiles.id"], name=op.f("fk_tickets_assignee_id_profiles")
        ),
        sa.ForeignKeyConstraint(
            ["category_id"], ["categories.id"], name=op.f("fk_tickets_category_id_categories")
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["profiles.id"], name=op.f("fk_tickets_customer_id_profiles")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tickets")),
    )
    op.create_index("ix_tickets_assignee_id", "tickets", ["assignee_id"], unique=False)
    op.create_index("ix_tickets_created_at", "tickets", ["created_at"], unique=False)
    op.create_index(
        "ix_tickets_customer_id_created_at", "tickets", ["customer_id", "created_at"], unique=False
    )
    op.create_index(
        "ix_tickets_first_response_due_at_unanswered",
        "tickets",
        ["first_response_due_at"],
        unique=False,
        postgresql_where=sa.text("first_responded_at IS NULL"),
    )
    op.create_index(
        "ix_tickets_resolution_due_at_unresolved",
        "tickets",
        ["resolution_due_at"],
        unique=False,
        postgresql_where=sa.text("resolved_at IS NULL"),
    )
    op.create_index(
        "ix_tickets_search_vector",
        "tickets",
        ["search_vector"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index("ix_tickets_status", "tickets", ["status"], unique=False)
    op.create_table(
        "ticket_comments",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=False),
        sa.Column(
            "author_role",
            sa.Enum(
                "customer",
                "agent",
                "admin",
                name="author_role",
                native_enum=False,
                length=32,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("is_internal", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "NOT (is_internal AND author_role = 'customer')",
            name=op.f("ck_ticket_comments_customers_cannot_write_internal"),
        ),
        sa.CheckConstraint(
            "length(body) BETWEEN 1 AND 10000", name=op.f("ck_ticket_comments_body_length")
        ),
        sa.ForeignKeyConstraint(
            ["author_id"], ["profiles.id"], name=op.f("fk_ticket_comments_author_id_profiles")
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["tickets.id"], name=op.f("fk_ticket_comments_ticket_id_tickets")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ticket_comments")),
    )
    op.create_index(
        "ix_ticket_comments_ticket_id_created_at",
        "ticket_comments",
        ["ticket_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "ticket_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column(
            "event_type",
            sa.Enum(
                "ticket_created",
                "status_changed",
                "assigned",
                "released",
                "category_changed",
                "priority_changed",
                "triage_completed",
                "triage_failed",
                "resolved_at_cleared",
                name="event_type",
                native_enum=False,
                length=32,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("from_value", sa.Text(), nullable=True),
        sa.Column("to_value", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["profiles.id"], name=op.f("fk_ticket_events_actor_id_profiles")
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["tickets.id"], name=op.f("fk_ticket_events_ticket_id_tickets")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ticket_events")),
    )
    op.create_index(
        "ix_ticket_events_ticket_id_created_at",
        "ticket_events",
        ["ticket_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_ticket_events_ticket_id_created_at", table_name="ticket_events")
    op.drop_table("ticket_events")
    op.drop_index("ix_ticket_comments_ticket_id_created_at", table_name="ticket_comments")
    op.drop_table("ticket_comments")
    op.drop_index("ix_tickets_status", table_name="tickets")
    op.drop_index("ix_tickets_search_vector", table_name="tickets", postgresql_using="gin")
    op.drop_index(
        "ix_tickets_resolution_due_at_unresolved",
        table_name="tickets",
        postgresql_where=sa.text("resolved_at IS NULL"),
    )
    op.drop_index(
        "ix_tickets_first_response_due_at_unanswered",
        table_name="tickets",
        postgresql_where=sa.text("first_responded_at IS NULL"),
    )
    op.drop_index("ix_tickets_customer_id_created_at", table_name="tickets")
    op.drop_index("ix_tickets_created_at", table_name="tickets")
    op.drop_index("ix_tickets_assignee_id", table_name="tickets")
    op.drop_table("tickets")
    op.drop_table("sla_policies")
    op.drop_table("profiles")
    op.drop_index("uq_categories_name_lower", table_name="categories")
    op.drop_table("categories")
