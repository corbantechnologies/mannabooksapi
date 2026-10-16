"""Statutory payroll: effective-dated rate sets, payroll lines, remittances.

Revision ID: 0003_payroll_statutory
Revises: 0002_refresh_tokens
Create Date: 2026-10-16
"""
import json
import uuid

import sqlalchemy as sa
from alembic import op

revision = "0003_payroll_statutory"
down_revision = "0002_refresh_tokens"
branch_labels = None
depends_on = None

DDL = [
    'CREATE TABLE IF NOT EXISTS statutory_rate_sets (\n\tid UUID NOT NULL, \n\tcountry VARCHAR(2) NOT NULL, \n\tname VARCHAR(120) NOT NULL, \n\teffective_from DATE NOT NULL, \n\trates JSONB NOT NULL, \n\tis_verified BOOLEAN NOT NULL, \n\tnotes TEXT, \n\tcreated_by_id UUID, \n\tcreated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_statutory_rate_sets_country_from UNIQUE (country, effective_from), \n\tFOREIGN KEY(created_by_id) REFERENCES users (id) ON DELETE SET NULL\n)',
    'CREATE INDEX IF NOT EXISTS ix_statutory_rate_sets_effective_from ON statutory_rate_sets (effective_from)',
    'CREATE TABLE IF NOT EXISTS payroll_lines (\n\tid UUID NOT NULL, \n\tshop_id UUID NOT NULL, \n\tdocument_id UUID NOT NULL, \n\temployee_id UUID, \n\trate_set_id UUID, \n\tperiod VARCHAR(7) NOT NULL, \n\tmode VARCHAR(30) NOT NULL, \n\temployee_name VARCHAR(255) NOT NULL, \n\tkra_pin VARCHAR(50), \n\tbasic_salary NUMERIC(14, 2) NOT NULL, \n\tallowances NUMERIC(14, 2) NOT NULL, \n\tcommissions NUMERIC(14, 2) NOT NULL, \n\tgross_pay NUMERIC(14, 2) NOT NULL, \n\tnssf_employee NUMERIC(14, 2) NOT NULL, \n\tshif NUMERIC(14, 2) NOT NULL, \n\thousing_levy_employee NUMERIC(14, 2) NOT NULL, \n\ttaxable_pay NUMERIC(14, 2) NOT NULL, \n\ttax_charged NUMERIC(14, 2) NOT NULL, \n\tpersonal_relief NUMERIC(14, 2) NOT NULL, \n\tpaye NUMERIC(14, 2) NOT NULL, \n\tcustom_deductions NUMERIC(14, 2) NOT NULL, \n\ttotal_deductions NUMERIC(14, 2) NOT NULL, \n\tnet_pay NUMERIC(14, 2) NOT NULL, \n\tnssf_employer NUMERIC(14, 2) NOT NULL, \n\thousing_levy_employer NUMERIC(14, 2) NOT NULL, \n\tnita NUMERIC(14, 2) NOT NULL, \n\tcreated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(shop_id) REFERENCES shops (id) ON DELETE CASCADE, \n\tFOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE, \n\tFOREIGN KEY(employee_id) REFERENCES employees (id) ON DELETE SET NULL, \n\tFOREIGN KEY(rate_set_id) REFERENCES statutory_rate_sets (id) ON DELETE SET NULL\n)',
    'CREATE INDEX IF NOT EXISTS ix_payroll_lines_document_id ON payroll_lines (document_id)',
    'CREATE INDEX IF NOT EXISTS ix_payroll_lines_employee_id ON payroll_lines (employee_id)',
    'CREATE INDEX IF NOT EXISTS ix_payroll_lines_period ON payroll_lines (period)',
    'CREATE INDEX IF NOT EXISTS ix_payroll_lines_shop_id ON payroll_lines (shop_id)',
    'CREATE TABLE IF NOT EXISTS payroll_remittances (\n\tid UUID NOT NULL, \n\tshop_id UUID NOT NULL, \n\tauthority VARCHAR(20) NOT NULL, \n\tperiod VARCHAR(7) NOT NULL, \n\tamount NUMERIC(14, 2) NOT NULL, \n\tpayment_date DATE NOT NULL, \n\tpayment_reference VARCHAR(100), \n\tpayment_channel VARCHAR(30) NOT NULL, \n\tnotes TEXT, \n\tcreated_by_id UUID, \n\tcreated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(shop_id) REFERENCES shops (id) ON DELETE CASCADE, \n\tFOREIGN KEY(created_by_id) REFERENCES users (id) ON DELETE SET NULL\n)',
    'CREATE INDEX IF NOT EXISTS ix_payroll_remittances_period ON payroll_remittances (period)',
    'CREATE INDEX IF NOT EXISTS ix_payroll_remittances_shop_id ON payroll_remittances (shop_id)',
]

# Frozen copy of app.modules.payroll.rates.SEED_RATE_SETS at the time of this revision.
SEED_RATE_SETS = json.loads("[{\"name\": \"Kenya - NSSF Year 3 (Feb 2025)\", \"effective_from\": \"2025-02-01\", \"rates\": {\"nssf\": {\"rate\": \"0.06\", \"lower_limit\": \"8000\", \"upper_limit\": \"72000\", \"employer_match\": true}, \"shif\": {\"rate\": \"0.0275\", \"minimum\": \"300\"}, \"housing_levy\": {\"employee_rate\": \"0.015\", \"employer_rate\": \"0.015\"}, \"nita\": {\"employer_per_employee\": \"50\"}, \"paye\": {\"bands\": [[\"24000\", \"0.10\"], [\"8333\", \"0.25\"], [\"467667\", \"0.30\"], [\"300000\", \"0.325\"], [null, \"0.35\"]], \"personal_relief\": \"2400\", \"deduct_nssf\": true, \"deduct_shif\": true, \"deduct_housing_levy\": true}}, \"is_verified\": true, \"notes\": \"NSSF LEL 8,000 / UEL 72,000 (max employee 4,320). SHIF 2.75% (min 300), AHL 1.5% + 1.5%, NITA 50. SHIF and AHL deductible for PAYE from 27 Dec 2024.\"}, {\"name\": \"Kenya - NSSF Year 4 (Feb 2026)\", \"effective_from\": \"2026-02-01\", \"rates\": {\"nssf\": {\"rate\": \"0.06\", \"lower_limit\": \"9000\", \"upper_limit\": \"108000\", \"employer_match\": true}, \"shif\": {\"rate\": \"0.0275\", \"minimum\": \"300\"}, \"housing_levy\": {\"employee_rate\": \"0.015\", \"employer_rate\": \"0.015\"}, \"nita\": {\"employer_per_employee\": \"50\"}, \"paye\": {\"bands\": [[\"24000\", \"0.10\"], [\"8333\", \"0.25\"], [\"467667\", \"0.30\"], [\"300000\", \"0.325\"], [null, \"0.35\"]], \"personal_relief\": \"2400\", \"deduct_nssf\": true, \"deduct_shif\": true, \"deduct_housing_levy\": true}}, \"is_verified\": false, \"notes\": \"NSSF LEL 9,000 / UEL 108,000 (max employee 6,480). CONFIRM against the NSSF circular, then mark as verified.\"}]")


def upgrade() -> None:
    for stmt in DDL:
        op.execute(stmt)
    for rs in SEED_RATE_SETS:
        op.execute(
            sa.text(
                "INSERT INTO statutory_rate_sets (id, country, name, effective_from, rates, is_verified, notes, created_at, updated_at) "
                "VALUES (CAST(:id AS UUID), 'KE', :name, CAST(:eff AS DATE), CAST(:rates AS JSONB), :verified, :notes, now(), now()) "
                "ON CONFLICT ON CONSTRAINT uq_statutory_rate_sets_country_from DO NOTHING"
            ).bindparams(
                id=str(uuid.uuid4()),
                name=rs["name"],
                eff=rs["effective_from"],
                rates=json.dumps(rs["rates"]),
                verified=rs["is_verified"],
                notes=rs["notes"],
            )
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS payroll_remittances")
    op.execute("DROP TABLE IF EXISTS payroll_lines")
    op.execute("DROP TABLE IF EXISTS statutory_rate_sets")
