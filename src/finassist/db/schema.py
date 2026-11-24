"""The single schema definition (ARCH §6.2). All tables exist from the first migration,
so later phases add behaviour, not disruptive migrations.

The transaction table is named `txn` because TRANSACTION is an SQL keyword.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Computed,
    Date,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)

metadata = MetaData()

ID = String(26)
NOW = text("(strftime('%Y-%m-%dT%H:%M:%fZ','now'))")


def _ts() -> list[Column]:  # type: ignore[type-arg]
    return [
        Column("created_at", String, nullable=False, server_default=NOW),
        Column("updated_at", String, nullable=False, server_default=NOW),
    ]


account = Table(
    "account",
    metadata,
    Column("id", ID, primary_key=True),
    Column("name", String, nullable=False, unique=True),
    Column("nickname", String, nullable=False),  # label sent to the LLM instead of institution/last4
    Column("type", String, nullable=False),
    Column("institution", String),
    Column("last4", String(4)),
    Column("currency", String(3), nullable=False, server_default="INR"),
    Column("opening_balance_minor", Integer, nullable=False, server_default="0"),
    Column("opening_balance_date", Date),
    Column("is_active", Boolean, nullable=False, server_default="1"),
    *_ts(),
    CheckConstraint(
        "type IN ('savings','current','credit_card','cash','wallet','loan','investment')",
        name="ck_account_type",
    ),
)

category = Table(
    "category",
    metadata,
    Column("id", ID, primary_key=True),
    Column("parent_id", ID, ForeignKey("category.id")),
    Column("name", String, nullable=False),
    Column("kind", String, nullable=False),
    Column("is_essential", Boolean, nullable=False, server_default="0"),
    Column("is_system", Boolean, nullable=False, server_default="0"),
    *_ts(),
    CheckConstraint("kind IN ('income','expense','transfer','investment')", name="ck_category_kind"),
    UniqueConstraint("parent_id", "name", name="uq_category_parent_name"),
)

merchant = Table(
    "merchant",
    metadata,
    Column("id", ID, primary_key=True),
    Column("canonical_name", String, nullable=False, unique=True),
    Column("default_category_id", ID, ForeignKey("category.id")),
    Column("is_subscription", Boolean, nullable=False, server_default="0"),
    Column("vpa_handles_json", Text),
    *_ts(),
)

merchant_alias = Table(
    "merchant_alias",
    metadata,
    Column("alias_key", String, primary_key=True),
    Column("merchant_id", ID, ForeignKey("merchant.id"), nullable=False),
    Column("source", String, nullable=False),
    CheckConstraint("source IN ('seed','user','confirmed_model')", name="ck_alias_source"),
)

tag = Table(
    "tag",
    metadata,
    Column("id", ID, primary_key=True),
    Column("name", String, nullable=False, unique=True),
)

mapping_profile = Table(
    "mapping_profile",
    metadata,
    Column("id", ID, primary_key=True),
    Column("name", String, nullable=False, unique=True),
    Column("header_signature", String, nullable=False, unique=True),
    Column("file_kind", String, nullable=False),
    Column("config_json", Text, nullable=False),
    Column("default_account_id", ID, ForeignKey("account.id")),
    Column("version", Integer, nullable=False, server_default="1"),
    Column("is_builtin", Boolean, nullable=False, server_default="0"),
    *_ts(),
)

import_source = Table(
    "import_source",
    metadata,
    Column("id", ID, primary_key=True),
    Column("file_name", String, nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("source_type", String, nullable=False),
    Column("account_id", ID, ForeignKey("account.id")),
    Column("mapping_profile", String),
    Column("row_count", Integer, nullable=False, server_default="0"),
    Column("period_start", Date),
    Column("period_end", Date),
    Column("statement_opening_minor", Integer),
    Column("statement_closing_minor", Integer),
    Column("imported_at", String, nullable=False, server_default=NOW),
    Column("status", String, nullable=False),
    Column("error", Text),
    CheckConstraint("status IN ('staged','committed','rolled_back','failed')", name="ck_import_status"),
    Index(
        "uq_import_committed", "sha256", "account_id", unique=True, sqlite_where=text("status = 'committed'")
    ),
)

source_row = Table(
    "source_row",
    metadata,
    Column("id", ID, primary_key=True),
    Column("import_source_id", ID, ForeignKey("import_source.id"), nullable=False),
    Column("row_index", Integer, nullable=False),
    Column("page", Integer),
    Column("raw_text", Text, nullable=False),
    Column("raw_fields_json", Text),
    Column("parsed_ok", Boolean, nullable=False, server_default="1"),
    UniqueConstraint("import_source_id", "row_index", name="uq_source_row_position"),
)

transfer_group = Table(
    "transfer_group",
    metadata,
    Column("id", ID, primary_key=True),
    Column("confidence", Integer, nullable=False),  # 0–100
    Column("status", String, nullable=False),
    Column("created_by", String, nullable=False),
    Column("created_at", String, nullable=False, server_default=NOW),
    CheckConstraint("status IN ('auto','confirmed','rejected')", name="ck_transfer_status"),
)

txn = Table(
    "txn",
    metadata,
    Column("id", ID, primary_key=True),
    Column("account_id", ID, ForeignKey("account.id"), nullable=False),
    Column("txn_date", Date, nullable=False),
    Column("value_date", Date),
    Column("amount_minor", Integer, nullable=False),
    Column("currency", String(3), nullable=False),
    Column(
        "direction",
        String,
        Computed("CASE WHEN amount_minor >= 0 THEN 'credit' ELSE 'debit' END", persisted=False),
    ),
    Column("orig_amount_minor", Integer),
    Column("orig_currency", String(3)),
    Column("raw_description", Text, nullable=False),
    Column("merchant_id", ID, ForeignKey("merchant.id")),
    Column("category_id", ID, ForeignKey("category.id")),
    Column("payment_method", String, nullable=False, server_default="other"),
    Column("is_transfer", Boolean, nullable=False, server_default="0"),
    Column("transfer_group_id", ID, ForeignKey("transfer_group.id")),
    Column("split_parent_id", ID, ForeignKey("txn.id")),
    Column("is_split_parent", Boolean, nullable=False, server_default="0"),
    Column("refund_of_txn_id", ID, ForeignKey("txn.id")),
    Column("confidence", Integer),  # 0–100
    Column("classification_source", String),
    Column("classification_ref", String),
    Column("needs_review", Boolean, nullable=False, server_default="0"),
    Column("user_locked", Boolean, nullable=False, server_default="0"),
    Column("fingerprint", String(64)),
    Column("external_ref", String),
    Column("notes", Text),
    Column("deleted_at", String),
    *_ts(),
    CheckConstraint(
        "payment_method IN ('upi','card','netbanking','neft_imps_rtgs','nach','atm','cash','cheque','other')",
        name="ck_txn_payment_method",
    ),
    CheckConstraint(
        "classification_source IS NULL OR classification_source IN "
        "('user','rule','merchant_map','heuristic','model','transfer_detector','split')",
        name="ck_txn_classification_source",
    ),
    Index("ix_txn_account_date", "account_id", "txn_date"),
    Index("ix_txn_category_date", "category_id", "txn_date"),
    Index("ix_txn_merchant_date", "merchant_id", "txn_date"),
    Index("ix_txn_date", "txn_date"),
    Index("ix_txn_transfer_group", "transfer_group_id"),
    Index("ix_txn_needs_review", "needs_review", sqlite_where=text("needs_review = 1")),
    Index(
        "uq_txn_fingerprint",
        "account_id",
        "fingerprint",
        unique=True,
        sqlite_where=text("fingerprint IS NOT NULL"),
    ),
    Index(
        "uq_txn_external_ref",
        "account_id",
        "external_ref",
        unique=True,
        sqlite_where=text("external_ref IS NOT NULL"),
    ),
)

transaction_source = Table(
    "transaction_source",
    metadata,
    Column("txn_id", ID, ForeignKey("txn.id"), nullable=False),
    Column("source_row_id", ID, ForeignKey("source_row.id"), nullable=False),
    Column("role", String, nullable=False),
    Column("merge_confidence", Integer),
    PrimaryKeyConstraint("txn_id", "source_row_id"),
    CheckConstraint("role IN ('primary','merged_duplicate')", name="ck_txn_source_role"),
)

transaction_tag = Table(
    "transaction_tag",
    metadata,
    Column("txn_id", ID, ForeignKey("txn.id"), nullable=False),
    Column("tag_id", ID, ForeignKey("tag.id"), nullable=False),
    PrimaryKeyConstraint("txn_id", "tag_id"),
)

rule = Table(
    "rule",
    metadata,
    Column("id", ID, primary_key=True),
    Column("matcher_json", Text, nullable=False),
    Column("action_json", Text, nullable=False),
    Column("priority", Integer, nullable=False, server_default="100"),
    Column("created_from_correction", Boolean, nullable=False, server_default="0"),
    Column("active", Boolean, nullable=False, server_default="1"),
    Column("hit_count", Integer, nullable=False, server_default="0"),
    *_ts(),
)

budget = Table(
    "budget",
    metadata,
    Column("id", ID, primary_key=True),
    Column("category_id", ID, ForeignKey("category.id")),  # NULL = overall
    Column("amount_minor", Integer, nullable=False),
    Column("rollover", Boolean, nullable=False, server_default="0"),
    Column("start_month", String(7), nullable=False),
    Column("end_month", String(7)),
    Column("alert_threshold_pct", Integer, nullable=False, server_default="80"),
    *_ts(),
)

goal = Table(
    "goal",
    metadata,
    Column("id", ID, primary_key=True),
    Column("name", String, nullable=False, unique=True),
    Column("target_minor", Integer, nullable=False),
    Column("target_date", Date, nullable=False),
    Column("linked_account_id", ID, ForeignKey("account.id")),
    *_ts(),
)

goal_contribution = Table(
    "goal_contribution",
    metadata,
    Column("id", ID, primary_key=True),
    Column("goal_id", ID, ForeignKey("goal.id"), nullable=False),
    Column("txn_id", ID, ForeignKey("txn.id")),
    Column("amount_minor", Integer, nullable=False),
    Column("contributed_on", Date, nullable=False),
)

recurring_series = Table(
    "recurring_series",
    metadata,
    Column("id", ID, primary_key=True),
    Column("merchant_id", ID, ForeignKey("merchant.id")),
    Column("label", String, nullable=False),
    Column("origin", String, nullable=False),
    Column("direction", String, nullable=False),
    Column("cadence", String, nullable=False),
    Column("cadence_days", Integer, nullable=False),
    Column("expected_amount_minor", Integer, nullable=False),
    Column("amount_tolerance_minor", Integer, nullable=False),
    Column("next_expected_date", Date),
    Column("expected_window_days", Integer, nullable=False, server_default="3"),
    Column("status", String, nullable=False),
    *_ts(),
    CheckConstraint("origin IN ('user','detected')", name="ck_recurring_origin"),
    CheckConstraint("direction IN ('debit','credit')", name="ck_recurring_direction"),
    CheckConstraint(
        "status IN ('active','new','increased','decreased','missing','cancelled','unused')",
        name="ck_recurring_status",
    ),
)

recurring_member = Table(
    "recurring_member",
    metadata,
    Column("series_id", ID, ForeignKey("recurring_series.id"), nullable=False),
    Column("txn_id", ID, ForeignKey("txn.id"), nullable=False),
    PrimaryKeyConstraint("series_id", "txn_id"),
)

statement_balance = Table(
    "statement_balance",
    metadata,
    Column("id", ID, primary_key=True),
    Column("account_id", ID, ForeignKey("account.id"), nullable=False),
    Column("import_source_id", ID, ForeignKey("import_source.id")),
    Column("as_of_date", Date, nullable=False),
    Column("kind", String, nullable=False),
    Column("balance_minor", Integer, nullable=False),
    Column("source_row_id", ID, ForeignKey("source_row.id")),
    CheckConstraint("kind IN ('opening','closing','running')", name="ck_statement_balance_kind"),
)

review_item = Table(
    "review_item",
    metadata,
    Column("id", ID, primary_key=True),
    Column("kind", String, nullable=False),
    Column("txn_ids_json", Text, nullable=False, server_default="[]"),
    Column("payload_json", Text, nullable=False, server_default="{}"),
    Column("suggested_resolution_json", Text),
    Column("priority", Integer, nullable=False, server_default="0"),
    Column("created_at", String, nullable=False, server_default=NOW),
    Column("resolved_at", String),
    Column("resolution_json", Text),
)

change_set = Table(
    "change_set",
    metadata,
    Column("id", ID, primary_key=True),
    Column("actor", String, nullable=False),
    Column("summary", Text, nullable=False),
    Column("created_at", String, nullable=False, server_default=NOW),
    Column("undone_at", String),
    CheckConstraint("actor IN ('user','rule','agent','import','system')", name="ck_change_set_actor"),
)

audit_log = Table(
    "audit_log",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("change_set_id", ID, ForeignKey("change_set.id"), nullable=False),
    Column("entity", String, nullable=False),
    Column("entity_id", String, nullable=False),
    Column("field", String, nullable=False),
    Column("old_value", Text),
    Column("new_value", Text),
    Column("actor", String, nullable=False),
    Column("reason", Text),
    Column("at", String, nullable=False, server_default=NOW),
    Index("ix_audit_entity", "entity", "entity_id"),
    Index("ix_audit_change_set", "change_set_id"),
)

query_receipt = Table(
    "query_receipt",
    metadata,
    Column("query_id", String, primary_key=True),
    Column("created_at", String, nullable=False, server_default=NOW),
    Column("tool", String, nullable=False),
    Column("spec_json", Text, nullable=False),
    Column("scope_text", Text, nullable=False),
    Column("sql_hash", String(64), nullable=False),
    Column("result_json", Text, nullable=False),
    Column("txn_ids_blob", LargeBinary, nullable=False),
)

fx_rate = Table(
    "fx_rate",
    metadata,
    Column("id", ID, primary_key=True),
    Column("from_currency", String(3), nullable=False),
    Column("to_currency", String(3), nullable=False),
    Column("rate", String, nullable=False),  # decimal string, never float
    Column("as_of_date", Date, nullable=False),
    Column("source", String, nullable=False),
)

preference = Table(
    "preference",
    metadata,
    Column("key", String, primary_key=True),
    Column("value_json", Text, nullable=False),
    Column("updated_at", String, nullable=False, server_default=NOW),
)

llm_call = Table(
    "llm_call",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("at", String, nullable=False, server_default=NOW),
    Column("role", String, nullable=False),  # chat | classify | receipt | mapping
    Column("provider", String, nullable=False),
    Column("model", String, nullable=False),
    Column("input_tokens", Integer, nullable=False, server_default="0"),
    Column("output_tokens", Integer, nullable=False, server_default="0"),
    Column("cache_read_tokens", Integer, nullable=False, server_default="0"),
    Column("cache_write_tokens", Integer, nullable=False, server_default="0"),
    Column("cost_micro_usd", Integer, nullable=False, server_default="0"),
    Column("redactions", Integer, nullable=False, server_default="0"),
    Column("stop_reason", String),
    Column("ok", Boolean, nullable=False, server_default="1"),
)

# DDL that SQLAlchemy Core cannot express. Applied by the first migration.
EXTRA_DDL: list[str] = [
    # NFR-7: the audit log is append-only.
    """CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON audit_log
       BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END""",
    """CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log
       BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END""",
    # A transaction's currency must equal its account's currency.
    """CREATE TRIGGER txn_currency_insert BEFORE INSERT ON txn
       WHEN NEW.currency != (SELECT currency FROM account WHERE id = NEW.account_id)
       BEGIN SELECT RAISE(ABORT, 'transaction currency differs from account currency'); END""",
    """CREATE TRIGGER txn_currency_update BEFORE UPDATE OF currency, account_id ON txn
       WHEN NEW.currency != (SELECT currency FROM account WHERE id = NEW.account_id)
       BEGIN SELECT RAISE(ABORT, 'transaction currency differs from account currency'); END""",
    # Invariant 4: a split child's amount is fixed once written (splits are recreated, not edited).
    """CREATE TRIGGER txn_split_child_amount_lock BEFORE UPDATE OF amount_minor ON txn
       WHEN OLD.split_parent_id IS NOT NULL
       BEGIN SELECT RAISE(ABORT, 'split child amounts cannot be edited; re-split the parent'); END""",
    # Full-text search over descriptions, notes and merchant names (FR-21).
    """CREATE VIRTUAL TABLE txn_fts USING fts5(txn_id UNINDEXED, raw_description, notes, merchant)""",
    """CREATE TRIGGER txn_fts_insert AFTER INSERT ON txn BEGIN
         INSERT INTO txn_fts(txn_id, raw_description, notes, merchant) VALUES (
           NEW.id, NEW.raw_description, COALESCE(NEW.notes, ''),
           COALESCE((SELECT canonical_name FROM merchant WHERE id = NEW.merchant_id), ''));
       END""",
    """CREATE TRIGGER txn_fts_update AFTER UPDATE OF raw_description, notes, merchant_id ON txn BEGIN
         DELETE FROM txn_fts WHERE txn_id = OLD.id;
         INSERT INTO txn_fts(txn_id, raw_description, notes, merchant) VALUES (
           NEW.id, NEW.raw_description, COALESCE(NEW.notes, ''),
           COALESCE((SELECT canonical_name FROM merchant WHERE id = NEW.merchant_id), ''));
       END""",
    """CREATE TRIGGER txn_fts_delete AFTER DELETE ON txn BEGIN
         DELETE FROM txn_fts WHERE txn_id = OLD.id;
       END""",
]
