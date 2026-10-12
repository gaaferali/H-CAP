import uuid

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.utils import timezone as django_timezone

class Tenant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    tenant_type = models.CharField(max_length=80)
    default_currency = models.CharField(max_length=3)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=django_timezone.now)

    def __str__(self):
        return self.name

    class Meta:
        db_table = "core_tenant"


class IdempotencyRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("Tenant", on_delete=models.PROTECT, related_name="idempotency_records")
    actor = models.ForeignKey("User", on_delete=models.PROTECT, related_name="idempotency_records")
    endpoint = models.CharField(max_length=255)
    idempotency_key = models.CharField(max_length=255)
    request_fingerprint = models.CharField(max_length=64)
    response_status = models.PositiveSmallIntegerField(default=201)
    response_body = models.JSONField(default=dict, encoder=DjangoJSONEncoder)
    created_at = models.DateTimeField(default=django_timezone.now)

    class Meta:
        db_table = "core_idempotencyrecord"
        constraints = [models.UniqueConstraint(fields=["tenant", "actor", "endpoint", "idempotency_key"], name="unique_idempotency_request")]


class SyncOperation(models.Model):
    class Status(models.TextChoices):
        APPLIED = "APPLIED", "Applied"
        DUPLICATE = "DUPLICATE", "Duplicate"
        CONFLICT = "CONFLICT", "Conflict"
        REJECTED = "REJECTED", "Rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="sync_operations")
    operation_id = models.CharField(max_length=160)
    operation_type = models.CharField(max_length=80)
    client_generated_id = models.CharField(max_length=160, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.APPLIED)
    request_hash = models.CharField(max_length=64, blank=True)
    validation_results = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(default=django_timezone.now)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "core_syncoperation"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "operation_id"],
                name="unique_sync_operation_per_tenant",
            )
        ]


class UserManager(BaseUserManager):
    def create_user(self, email, full_name, tenant=None, password=None, **extra_fields):
        if not email:
            raise ValueError("Email is required")
        user = self.model(email=self.normalize_email(email), full_name=full_name, tenant=tenant, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, full_name="Administrator", password=None, **extra_fields):
        tenant = extra_fields.pop("tenant", None) or Tenant.objects.create(
            name="System Tenant", tenant_type="ADMIN", default_currency="USD"
        )
        extra_fields.setdefault("role", User.Role.ADMIN)
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self.create_user(email, full_name, tenant, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    class Role(models.TextChoices):
        ADMIN = "ADMIN", "Admin"
        FIELD_OFFICER = "FIELD_OFFICER", "Field officer"
        FINANCE = "FINANCE", "Finance"
        REVIEWER = "REVIEWER", "Reviewer"
        SUPPORT = "SUPPORT", "Support"
        MANAGER = "MANAGER", "Manager"
        AUDITOR = "AUDITOR", "Auditor"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="users")
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=255)
    role = models.CharField(max_length=32, choices=Role.choices)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=django_timezone.now)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]
    objects = UserManager()

    def __str__(self):
        return self.email

    class Meta:
        db_table = "core_user"

