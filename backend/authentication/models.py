from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.utils import timezone


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        abstract = True


class UserAccount(TimestampedModel):
    id = models.CharField(max_length=64, primary_key=True)
    email = models.EmailField(unique=True, db_index=True)
    password = models.TextField()

    def check_password(self, raw_password: str) -> bool:
        if check_password(raw_password, self.password):
            return True
        # Legacy: raw bcrypt hash without Django prefix
        if self.password.startswith('$2b$') or self.password.startswith('$2a$'):
            import bcrypt
            return bcrypt.checkpw(raw_password.encode('utf-8'), self.password.encode('utf-8'))
        return False

    def set_password(self, raw_password: str) -> None:
        self.password = make_password(raw_password)

    first_name = models.CharField(max_length=150, blank=True, default="")
    last_name = models.CharField(max_length=150, blank=True, default="")
    is_active = models.BooleanField(default=True, db_index=True)
    is_staff = models.BooleanField(default=False, db_index=True)
    date_joined = models.DateTimeField(default=timezone.now, db_index=True)
    profile = models.JSONField(default=dict, blank=True)
    company_name = models.CharField(max_length=255, blank=True, default="")
    company_url = models.URLField(blank=True, default="")
    avatar_url = models.URLField(blank=True, default="")
    extra = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "users"
        indexes = [
            models.Index(fields=["email"]),
            models.Index(fields=["created_at"]),
        ]


class PasswordResetToken(TimestampedModel):
    id = models.CharField(max_length=128, primary_key=True)
    email = models.EmailField(db_index=True)
    token = models.CharField(max_length=255, unique=True, db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    used = models.BooleanField(default=False, db_index=True)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "password_resets"
        indexes = [
            models.Index(fields=["email", "used"]),
            models.Index(fields=["expires_at"]),
        ]



