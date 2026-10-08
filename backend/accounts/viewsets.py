import json
from hashlib import sha256

from django.db import transaction
from rest_framework import status, viewsets
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from reporting.audit import audit, json_safe

from .models import IdempotencyRecord


class TenantScopedModelViewSet(viewsets.ModelViewSet):
    tenant_field = "tenant"

    def tenant_filter(self):
        return {self.tenant_field: self.request.user.tenant}

    def get_queryset(self):
        if self.request.user.is_superuser:
            return self.queryset.all()
        return self.queryset.filter(**self.tenant_filter())

    def create(self, request, *args, **kwargs):
        key = request.headers.get("Idempotency-Key", "").strip()
        if not key:
            return super().create(request, *args, **kwargs)
        fingerprint = sha256(json.dumps(request.data, sort_keys=True, default=str).encode("utf-8")).hexdigest()
        with transaction.atomic():
            record, created = IdempotencyRecord.objects.get_or_create(
                tenant=request.user.tenant,
                actor=request.user,
                endpoint=request.path,
                idempotency_key=key,
                defaults={"request_fingerprint": fingerprint, "response_body": {}},
            )
            record = IdempotencyRecord.objects.select_for_update().get(pk=record.pk)
            if not created:
                if record.request_fingerprint != fingerprint:
                    raise ValidationError({"idempotency_key": "This idempotency key was already used with different data."})
                return Response(record.response_body, status=record.response_status)
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            self.perform_create(serializer)
            response = Response(serializer.data, status=status.HTTP_201_CREATED, headers=self.get_success_headers(serializer.data))
            record.request_fingerprint = fingerprint
            record.response_status = response.status_code
            record.response_body = json_safe(response.data)
            record.save(update_fields=["request_fingerprint", "response_status", "response_body"])
            return response

    def perform_create(self, serializer):
        kwargs = {}
        if "tenant" in [field.name for field in serializer.Meta.model._meta.fields]:
            kwargs["tenant"] = self.request.user.tenant
        if "created_by" in [field.name for field in serializer.Meta.model._meta.fields]:
            kwargs["created_by"] = self.request.user
        instance = serializer.save(**kwargs)
        audit(self.request.user, f"{serializer.Meta.model.__name__.upper()}_CREATED", instance, after=serializer.data)

    def perform_update(self, serializer):
        before = self.get_serializer(serializer.instance).data
        instance = serializer.save()
        audit(self.request.user, f"{serializer.Meta.model.__name__.upper()}_UPDATED", instance, before=before, after=serializer.data)

    def destroy(self, request, *args, **kwargs):
        raise PermissionDenied("Deletion is disabled to preserve auditability; use a permitted status change instead")


class RoleProtectedTenantViewSet(TenantScopedModelViewSet):
    allowed_roles: set[str] = set()
    write_roles: set[str] | None = None

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if request.user.role not in self.allowed_roles:
            raise PermissionDenied("Your role does not have permission for this resource")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and self.write_roles is not None and request.user.role not in self.write_roles:
            raise PermissionDenied("Your role has read-only access to this resource")
