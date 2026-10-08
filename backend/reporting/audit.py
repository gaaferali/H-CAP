import json
import uuid

from django.core.serializers.json import DjangoJSONEncoder

from .models import AuditEvent


def json_safe(value):
    """Return a recursively JSON-native copy of arbitrary model/API data."""
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder, default=str))


def audit(user, action, entity, before=None, after=None, tenant=None):
    tenant = tenant or getattr(user, "tenant", None)
    if tenant:
        AuditEvent.objects.create(
            tenant=tenant,
            actor=user,
            action=action,
            entity_type=entity.__class__.__name__,
            entity_id=str(entity.pk),
            before=json_safe(before or {}),
            after=json_safe(after or {}),
            correlation_id=str(uuid.uuid4()),
        )
