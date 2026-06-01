"""
Safe migration command with PostgreSQL advisory lock protection.

Prevents concurrent migration execution across multiple Azure App Service instances
during rolling deploys or scale-out scenarios.

Usage:
    python manage.py migrate_safe

Environment variables:
    MIGRATION_LOCK_ID: Advisory lock ID (default: 2147000001)
    MIGRATION_LOCK_TIMEOUT: Max seconds to wait for lock (default: 300)
    MIGRATION_POLL_INTERVAL: Seconds between lock attempts (default: 5)
"""
import os
import time
import sys
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = 'Run migrations with PostgreSQL advisory lock protection'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Use a deterministic lock ID in the safe 32-bit positive range
        self.lock_id = int(os.getenv("MIGRATION_LOCK_ID", "2147000001"))
        self.lock_timeout = int(os.getenv("MIGRATION_LOCK_TIMEOUT", "300"))  # 5 minutes
        self.poll_interval = int(os.getenv("MIGRATION_POLL_INTERVAL", "5"))  # 5 seconds

    def add_arguments(self, parser):
        parser.add_argument(
            '--no-lock',
            action='store_true',
            help='Skip advisory lock (use for local dev only)',
        )
        parser.add_argument(
            '--fake',
            action='store_true',
            help='Mark migrations as run without actually running them',
        )
        parser.add_argument(
            '--fake-initial',
            action='store_true',
            help='Detect and fake initial migrations',
        )

    def handle(self, *args, **options):
        no_lock = options.get('no_lock', False)

        if no_lock:
            self.stdout.write(
                self.style.WARNING("⚠ Skipping advisory lock (--no-lock mode)")
            )
            self._run_migrations(options)
            return

        # Try to acquire lock
        if not self._acquire_lock():
            self.stdout.write(
                self.style.ERROR(
                    f"✗ Failed to acquire migration lock after {self.lock_timeout}s. "
                    f"Another instance may be running migrations or the lock is stuck."
                )
            )
            self.stdout.write(
                "To manually release stuck locks, run in psql:\n"
                "  SELECT pg_advisory_unlock_all();"
            )
            sys.exit(1)

        try:
            self._run_migrations(options)
        finally:
            self._release_lock()

    def _acquire_lock(self):
        """Try to acquire PostgreSQL advisory lock with timeout."""
        self.stdout.write(
            f"Attempting to acquire migration lock "
            f"(id={self.lock_id}, timeout={self.lock_timeout}s)..."
        )

        start_time = time.time()
        attempt = 0

        while (time.time() - start_time) < self.lock_timeout:
            attempt += 1

            with connection.cursor() as cursor:
                # pg_try_advisory_lock returns true if lock acquired, false if already held
                cursor.execute("SELECT pg_try_advisory_lock(%s)", [self.lock_id])
                acquired = cursor.fetchone()[0]

                if acquired:
                    elapsed = time.time() - start_time
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"✓ Migration lock acquired (waited {elapsed:.1f}s, attempt {attempt})"
                        )
                    )
                    return True

            # Lock not acquired, wait and retry
            if attempt == 1:
                self.stdout.write(
                    "Migration lock held by another instance, waiting..."
                )

            time.sleep(self.poll_interval)

        return False

    def _release_lock(self):
        """Release PostgreSQL advisory lock."""
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [self.lock_id])
                self.stdout.write(self.style.SUCCESS("✓ Migration lock released"))
        except Exception as e:
            self.stdout.write(
                self.style.WARNING(f"⚠ Failed to release lock: {e}")
            )

    def _run_migrations(self, options):
        """Run Django migrations with configured options."""
        self.stdout.write("Running migrations...")
        start_time = time.time()

        # Build arguments for migrate command
        migrate_args = ['--noinput']
        if options.get('fake'):
            migrate_args.append('--fake')
        if options.get('fake_initial'):
            migrate_args.append('--fake-initial')

        try:
            call_command('migrate', *migrate_args, verbosity=options.get('verbosity', 1))
            elapsed = time.time() - start_time
            self.stdout.write(
                self.style.SUCCESS(
                    f"✓ Migrations completed successfully ({elapsed:.1f}s)"
                )
            )
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f"✗ Migration failed: {e}")
            )
            raise
