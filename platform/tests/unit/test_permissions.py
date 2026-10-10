from uuid import uuid4

import pytest

from digital360.core.permissions import (
    CLIENT_ROLE_PERMISSIONS,
    STAFF_ROLE_PERMISSIONS,
    ClientRole,
    Permission,
    Principal,
    StaffRole,
)

# Permissions qui ne doivent jamais être accordées à un client, quel que soit son rôle
STAFF_ONLY = {
    Permission.PASSPORT_UPDATE,
    Permission.PAYMENT_REFUND,
    Permission.WEBSITE_PROJECT_TRANSITION,
    Permission.DEPLOYMENT_EXECUTE,
    Permission.TASK_MANAGE,
    Permission.STAFF_MANAGE,
    Permission.AUDIT_READ,
    Permission.CONFIG_MANAGE,
}


def test_should_define_permissions_for_every_role() -> None:
    assert set(CLIENT_ROLE_PERMISSIONS) == set(ClientRole)
    assert set(STAFF_ROLE_PERMISSIONS) == set(StaffRole)


@pytest.mark.parametrize("role", list(ClientRole))
def test_should_never_grant_staff_only_permissions_to_clients(role: ClientRole) -> None:
    assert not CLIENT_ROLE_PERMISSIONS[role] & STAFF_ONLY


def test_should_give_all_permissions_to_admin() -> None:
    assert STAFF_ROLE_PERMISSIONS[StaffRole.ADMIN] == frozenset(Permission)


def test_should_keep_billing_away_from_client_member() -> None:
    member = CLIENT_ROLE_PERMISSIONS[ClientRole.CLIENT_MEMBER]

    assert Permission.INVOICE_READ not in member
    assert Permission.ORDER_CREATE not in member
    assert member < CLIENT_ROLE_PERMISSIONS[ClientRole.CLIENT_OWNER]


def test_should_reserve_refunds_to_finance_and_admin() -> None:
    allowed = {
        role for role, perms in STAFF_ROLE_PERMISSIONS.items() if Permission.PAYMENT_REFUND in perms
    }

    assert allowed == {StaffRole.ADMIN, StaffRole.FINANCE}


def test_should_scope_client_permission_to_its_organization() -> None:
    own_org, other_org = uuid4(), uuid4()
    owner = Principal(user_id=uuid4(), memberships={own_org: ClientRole.CLIENT_OWNER})

    assert owner.can(Permission.ORDER_CREATE, own_org)
    assert not owner.can(Permission.ORDER_CREATE, other_org)
    assert not owner.can(Permission.ORDER_CREATE)


def test_should_grant_staff_permission_on_any_organization() -> None:
    finance = Principal(user_id=uuid4(), staff_roles=frozenset({StaffRole.FINANCE}))

    assert finance.is_staff
    assert finance.can(Permission.PAYMENT_REFUND, uuid4())
    assert not finance.can(Permission.DEPLOYMENT_EXECUTE, uuid4())


def test_should_combine_multiple_staff_roles() -> None:
    principal = Principal(
        user_id=uuid4(), staff_roles=frozenset({StaffRole.FINANCE, StaffRole.DEVELOPER})
    )

    assert principal.can(Permission.PAYMENT_REFUND)
    assert principal.can(Permission.DEPLOYMENT_EXECUTE)
