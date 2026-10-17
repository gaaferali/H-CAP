from decimal import Decimal

from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q, Sum
from django.utils import timezone
from rest_framework import serializers

from accounts.models import IdempotencyRecord, Tenant, User
from assistance.models import Beneficiary, CashEntitlement, Enrollment, Household, HouseholdEligibility, HouseholdEnrollment
from assistance.serializer_helpers import (
    approved_household_modality,
    household_member_count,
    household_member_details,
    household_reference,
    national_id_digest,
    national_id_reference,
    normalize_national_id,
    normalize_phone_number,
    safe_beneficiary_reference,
)
from automation.models import AISignal, AutomationExecution, AutomationRule, ReviewTask
from operations.models import (
    Complaint,
    ComplaintAIAnalysis,
    DistributionAllocation,
    DistributionEvent,
    DistributionIssue,
    NFIEntitlement,
    NFIItem,
    PaymentDeliveryReview,
    StockMovement,
    Warehouse,
)
from payments.models import PaymentBatch, PaymentChannelConfig, PaymentEvent, PaymentInstruction, ReconciliationItem
from programs.models import ActivityDependency, Budget, Program, ProgramActivity
from reporting.models import AuditEvent



# Returns tenant details used by platform administration pages.
class TenantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tenant
        fields = "__all__"


# Validates user details and returns safe profile data without passwords.
class UserSerializer(serializers.ModelSerializer):
    tenant = TenantSerializer(read_only=True)
    tenant_id = serializers.PrimaryKeyRelatedField(source="tenant", queryset=Tenant.objects.all(), write_only=True, required=False)
    password = serializers.CharField(write_only=True, required=True)

    class Meta:
        model = User
        fields = ["id", "tenant", "tenant_id", "email", "full_name", "role", "is_active", "is_staff", "is_superuser", "created_at", "password"]
        read_only_fields = ["id", "tenant", "created_at"]

    def create(self, validated_data):
        password = validated_data.pop("password")
        user = User(**validated_data)
        try:
            validate_password(password, user=user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)}) from exc
        user.set_password(password)
        user.save()
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        for key, value in validated_data.items():
            setattr(instance, key, value)
        if password:
            try:
                validate_password(password, user=instance)
            except DjangoValidationError as exc:
                raise serializers.ValidationError({"password": list(exc.messages)}) from exc
            instance.set_password(password)
        instance.save()
        return instance


# Checks the email and password before the login view creates JWT tokens.
class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        user = authenticate(username=attrs["email"], password=attrs["password"])
        if not user or not user.is_active:
            raise serializers.ValidationError("Email or password is incorrect.")
        attrs["user"] = user
        return attrs
