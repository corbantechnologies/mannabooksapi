import json
import uuid
from typing import Optional, Set
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.identity import User, Shop, ShopMember

ROLE_PRESET_PERMISSIONS = {
    "OWNER": {
        "manage_documents", "canCreateDocuments", "manage_clients", "canEditClients",
        "manage_products", "manage_inventory_ops", "operate_pos", "manage_logistics",
        "manage_crm", "manage_expenses", "view_analytics", "view_finance", "canViewFinance",
        "export_reports", "canExportReports", "manage_payroll", "manage_team", "manage_settings",
        "view_cost_prices", "approve_requests"
    },
    "ADMIN": {
        "manage_documents", "canCreateDocuments", "manage_clients", "canEditClients",
        "manage_products", "manage_inventory_ops", "operate_pos", "manage_logistics",
        "manage_crm", "manage_expenses", "view_analytics", "view_finance", "canViewFinance",
        "export_reports", "canExportReports", "manage_payroll", "manage_team", "manage_settings",
        "view_cost_prices", "approve_requests"
    },
    "MANAGER": {
        "manage_documents", "canCreateDocuments", "manage_clients", "canEditClients",
        "manage_products", "manage_inventory_ops", "operate_pos", "manage_logistics",
        "manage_crm", "manage_expenses", "view_analytics", "view_finance", "canViewFinance",
        "export_reports", "canExportReports", "manage_payroll", "view_cost_prices", "approve_requests"
    },
    "ACCOUNTANT": {
        "manage_documents", "canCreateDocuments", "manage_clients", "canEditClients",
        "manage_expenses", "view_analytics", "view_finance", "canViewFinance",
        "export_reports", "canExportReports", "manage_payroll", "view_cost_prices", "approve_requests"
    },
    "STOREKEEPER": {
        "manage_products", "manage_inventory_ops", "manage_logistics", "canCreateDocuments"
    },
    "CASHIER": {
        "operate_pos", "manage_documents", "canCreateDocuments", "manage_clients", "canEditClients"
    },
    "DISPATCHER": {
        "manage_logistics", "manage_documents", "canCreateDocuments"
    },
    "SALES_REP": {
        "manage_crm", "manage_documents", "canCreateDocuments", "manage_clients", "canEditClients"
    },
    "VIEWER": {
        "view_analytics", "view_finance", "canViewFinance"
    },
    "EMPLOYEE": set(),
}


class WorkspaceContext:
    def __init__(self, user: User, shop: Shop, membership: Optional[ShopMember]):
        self.user = user
        self.shop = shop
        self.membership = membership

    def enforce_permission(self, permission: str):
        if self.user.is_super_admin:
            return

        if not self.membership or not self.membership.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access Denied. You do not belong to this workspace.",
            )

        role = self.membership.role
        if role in ("OWNER", "ADMIN"):
            return

        preset_perms = ROLE_PRESET_PERMISSIONS.get(role, set())
        if permission in preset_perms:
            return

        # Check custom override JSON
        if self.membership.custom_permissions:
            try:
                custom_map = json.loads(self.membership.custom_permissions)
                if custom_map.get(permission) is True:
                    return
            except Exception:
                pass

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access Denied. Role '{role}' lacks permission for this action.",
        )
