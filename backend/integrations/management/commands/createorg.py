"""Bootstrap a Saramsa workspace for a fresh deployment.

Saramsa is invite-only, so a brand-new install has no way to create the
first user via the web UI. This command seeds an organization and its
owner so the platform can be operated and further invitations issued.

Usage:
    python manage.py createorg --org-name "Acme" \
        --owner-email admin@acme.com \
        --owner-password '<password>' \
        --first-name Admin --last-name User

If --owner-password is omitted, the command prompts interactively. If a
user with the given email already exists, the existing account is used
and the new organization is attached to them.
"""

from __future__ import annotations

import uuid
from getpass import getpass

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from authentication.models import UserAccount
from integrations.services import get_organization_service


class Command(BaseCommand):
    help = (
        "Bootstrap a Saramsa workspace: create (or attach) the owner user "
        "and create the organization with that user as owner."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument("--org-name", required=True, help="Workspace name, e.g. 'Acme Corp'.")
        parser.add_argument("--owner-email", required=True, help="Email of the workspace owner.")
        parser.add_argument("--owner-password", default=None, help="Password for a new owner (omit to be prompted).")
        parser.add_argument("--first-name", default="", help="Owner's first name (only used for new users).")
        parser.add_argument("--last-name", default="", help="Owner's last name (only used for new users).")
        parser.add_argument("--description", default="", help="Optional workspace description.")

    def handle(self, *args, **options) -> None:
        org_name = options["org_name"].strip()
        email = options["owner_email"].strip().lower()
        if not org_name:
            raise CommandError("--org-name cannot be empty.")
        if not email:
            raise CommandError("--owner-email cannot be empty.")

        with transaction.atomic():
            user = UserAccount.objects.filter(email=email).first()
            if user is None:
                password = options["owner_password"]
                if not password:
                    password = getpass("Owner password: ")
                if not password or len(password) < 6:
                    raise CommandError("Password must be at least 6 characters.")

                user = UserAccount(
                    id=f"user_{uuid.uuid4().hex[:12]}",
                    email=email,
                    first_name=options["first_name"],
                    last_name=options["last_name"],
                    profile={"role": "admin"},
                    is_staff=True,
                )
                user.set_password(password)
                user.save()
                self.stdout.write(self.style.SUCCESS(f"Created user {user.id} ({email})."))
            else:
                self.stdout.write(f"User {email} already exists ({user.id}); attaching to new workspace.")

            org = get_organization_service().create_organization(
                name=org_name,
                description=options["description"],
                created_by_user_id=str(user.id),
            )

            profile = dict(user.profile or {})
            profile["active_organization_id"] = org["id"]
            user.profile = profile
            user.save(update_fields=["profile", "updated_at"])

        self.stdout.write(self.style.SUCCESS(
            f"Created organization '{org_name}' (id={org['id']}) with owner {email}."
        ))
