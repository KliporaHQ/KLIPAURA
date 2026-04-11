"""Add compliance fields to existing tables

Revision ID: 002_add_compliance_fields
Revises: 001_initial_schema
Create Date: 2026-04-03 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = '002_add_compliance_fields'
down_revision = '001_initial_schema'
branch_labels = None
depends_on = None


def upgrade():
    # Add compliance fields to opportunities table
    op.add_column('opportunities', sa.Column('geo_target', sa.String(8), nullable=False, server_default='AE'))
    op.add_column('opportunities', sa.Column('compliance_score', sa.Integer, nullable=False, server_default='100'))
    op.add_column('opportunities', sa.Column('state', sa.String(32), nullable=True))
    op.add_column('opportunities', sa.Column('compliance_data', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    
    # Add compliance fields to content_items table
    op.add_column('content_items', sa.Column('geo_target', sa.String(8), nullable=False, server_default='AE'))
    op.add_column('content_items', sa.Column('compliance_score', sa.Integer, nullable=False, server_default='100'))
    op.add_column('content_items', sa.Column('required_disclosure', sa.Text, nullable=True))
    
    # Add compliance fields to opportunity_sources table
    op.add_column('opportunity_sources', sa.Column('is_uae_compliant', sa.Boolean, nullable=False, server_default='true'))
    op.add_column('opportunity_sources', sa.Column('geo_restrictions', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    
    # Add new compliance-specific columns to risk_flags
    op.add_column('risk_flags', sa.Column('geo_target', sa.String(8), nullable=True))
    op.add_column('risk_flags', sa.Column('auto_block', sa.Boolean, nullable=False, server_default='false'))
    
    # Create indexes for compliance queries
    op.create_index('ix_opportunities_geo_target', 'opportunities', ['geo_target'])
    op.create_index('ix_opportunities_compliance_score', 'opportunities', ['compliance_score'])
    op.create_index('ix_opportunities_state', 'opportunities', ['state'])
    op.create_index('ix_content_items_geo_target', 'content_items', ['geo_target'])
    op.create_index('ix_content_items_compliance_score', 'content_items', ['compliance_score'])
    op.create_index('ix_risk_flags_geo_target', 'risk_flags', ['geo_target'])
    op.create_index('ix_risk_flags_auto_block', 'risk_flags', ['auto_block'])


def downgrade():
    # Remove indexes
    op.drop_index('ix_risk_flags_auto_block', table_name='risk_flags')
    op.drop_index('ix_risk_flags_geo_target', table_name='risk_flags')
    op.drop_index('ix_content_items_compliance_score', table_name='content_items')
    op.drop_index('ix_content_items_geo_target', table_name='content_items')
    op.drop_index('ix_opportunities_state', table_name='opportunities')
    op.drop_index('ix_opportunities_compliance_score', table_name='opportunities')
    op.drop_index('ix_opportunities_geo_target', table_name='opportunities')
    
    # Remove columns
    op.drop_column('risk_flags', 'auto_block')
    op.drop_column('risk_flags', 'geo_target')
    op.drop_column('opportunity_sources', 'geo_restrictions')
    op.drop_column('opportunity_sources', 'is_uae_compliant')
    op.drop_column('content_items', 'required_disclosure')
    op.drop_column('content_items', 'compliance_score')
    op.drop_column('content_items', 'geo_target')
    op.drop_column('opportunities', 'compliance_data')
    op.drop_column('opportunities', 'state')
    op.drop_column('opportunities', 'compliance_score')
    op.drop_column('opportunities', 'geo_target')
