"""Add waitlist_leads table for email capture

Revision ID: 003_add_waitlist_leads
Revises: 002_add_compliance_fields
Create Date: 2026-04-03 12:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = '003_add_waitlist_leads'
down_revision = '002_add_compliance_fields'
branch_labels = None
depends_on = None


def upgrade():
    # Create waitlist_leads table
    op.create_table(
        'waitlist_leads',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(128), nullable=True),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('source', sa.String(64), nullable=True, server_default='landing_page'),
        sa.Column('referred_by', sa.String(128), nullable=True),
        sa.Column('status', sa.String(32), nullable=False, server_default='pending'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email')
    )
    
    # Create indexes
    op.create_index('ix_waitlist_leads_email', 'waitlist_leads', ['email'])
    op.create_index('ix_waitlist_leads_status', 'waitlist_leads', ['status'])
    op.create_index('ix_waitlist_leads_created_at', 'waitlist_leads', ['created_at'])
    
    # Add UUID extension if not exists
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    
    # Set default values for timestamps
    op.execute("""
        ALTER TABLE waitlist_leads 
        ALTER COLUMN created_at SET DEFAULT now(),
        ALTER COLUMN updated_at SET DEFAULT now()
    """)


def downgrade():
    # Drop indexes
    op.drop_index('ix_waitlist_leads_created_at', table_name='waitlist_leads')
    op.drop_index('ix_waitlist_leads_status', table_name='waitlist_leads')
    op.drop_index('ix_waitlist_leads_email', table_name='waitlist_leads')
    
    # Drop table
    op.drop_table('waitlist_leads')
