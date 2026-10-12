"""Django integration boundary for the external AI and automation source folder.
This module only maps the model names
used by that source to the regular Django apps that own those models now.
"""

from importlib import util
from pathlib import Path
from types import ModuleType
import sys

from django.conf import settings
from django.db.models import Q

from accounts.models import SyncOperation, Tenant
from assistance.models import Beneficiary, Enrollment, Household
from automation.models import AISignal, AutomationExecution, AutomationRule, ReviewTask
from operations.models import Complaint, ComplaintAIAnalysis
from payments.models import PaymentBatch, PaymentEvent, PaymentInstruction, ReconciliationItem
from programs.models import Budget, Program
from reporting.models import AuditEvent


AI_AUTOMATION_ENABLED = True
SOURCE_ROOT = Path(settings.BASE_DIR) / "Ai and automation"
_SOURCE_PACKAGE = "_hcap_ai_automation_source"


class _HouseholdManagerAdapter:
    """Translate the source folder's former field name at the database edge."""

    def __getattr__(self, name):
        return getattr(Household.objects, name)

    @staticmethod
    def _map_fields(values):
        mapped = dict(values)
        if "client_generated_id" in mapped:
            mapped["registration_reference"] = mapped.pop("client_generated_id")
        return mapped

    def filter(self, *args, **kwargs):
        return Household.objects.filter(*args, **self._map_fields(kwargs))

    def create(self, **kwargs):
        return Household.objects.create(**self._map_fields(kwargs))


class _HouseholdModelAdapter:
    objects = _HouseholdManagerAdapter()


def _register_package(name, path):
    module = sys.modules.get(name)
    if module is None:
        module = ModuleType(name)
        module.__package__ = name
        module.__path__ = [str(path)]
        sys.modules[name] = module
    return module


def _model_module(name):
    module = ModuleType(name)
    model_names = {
        "AISignal": AISignal,
        "AutomationExecution": AutomationExecution,
        "AutomationRule": AutomationRule,
        "Beneficiary": Beneficiary,
        "PaymentBatch": PaymentBatch,
        "PaymentInstruction": PaymentInstruction,
        "PaymentEvent": PaymentEvent,
        "Budget": Budget,
        "ReconciliationItem": ReconciliationItem,
        "ReviewTask": ReviewTask,
        "SyncOperation": SyncOperation,
        "AuditEvent": AuditEvent,
        "Complaint": Complaint,
        "ComplaintAIAnalysis": ComplaintAIAnalysis,
        "Enrollment": Enrollment,
        "Household": Household,
        "Program": Program,
        "Tenant": Tenant,
    }
    module.__dict__.update(model_names)
    return module


def _load_module(name, relative_path):
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    path = SOURCE_ROOT / relative_path
    if not path.is_file():
        raise RuntimeError(f"AI and automation source file is missing: {path}")
    spec = util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load AI and automation source file: {path}")
    module = util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


def _install_source_namespace():
    _register_package(_SOURCE_PACKAGE, SOURCE_ROOT)
    _register_package(f"{_SOURCE_PACKAGE}.ai", SOURCE_ROOT / "ai")
    _register_package(f"{_SOURCE_PACKAGE}.management", SOURCE_ROOT / "management")
    _register_package(
        f"{_SOURCE_PACKAGE}.management.commands",
        SOURCE_ROOT / "management" / "commands",
    )

    source_models_name = f"{_SOURCE_PACKAGE}.models"
    if source_models_name not in sys.modules:
        sys.modules[source_models_name] = _model_module(source_models_name)

    # Two source files still use the old import spelling.  These are runtime-only
    # namespaces, not a restored ``core`` Django app or copied implementation.
    core_package = _register_package("core", SOURCE_ROOT)
    if "core.models" not in sys.modules:
        core_models = _model_module("core.models")
        core_models.Household = _HouseholdModelAdapter
        sys.modules["core.models"] = core_models
        core_package.models = core_models


_install_source_namespace()
_services = _load_module(f"{_SOURCE_PACKAGE}.services", "services.py")
sys.modules["core.services"] = _services
sys.modules["core"].services = _services
_complaints = _load_module(f"{_SOURCE_PACKAGE}.complaint_ai", "complaint_ai.py")
_duplicates = _load_module(
    f"{_SOURCE_PACKAGE}.ai.duplicate_detector",
    Path("ai") / "duplicate_detector.py",
)
_import_command = _load_module(
    f"{_SOURCE_PACKAGE}.management.commands.import_data",
    Path("management") / "commands" / "import_data.py",
)

# The old names are needed only while importing the unchanged source files.
# Remove them immediately so the running project has no ``core`` package.
sys.modules.pop("core.services", None)
sys.modules.pop("core.models", None)
sys.modules.pop("core", None)


analyze_complaint = _complaints.analyze_complaint
run_anomaly_detection = _services.run_anomaly_detection
run_automated_reconciliation = _services.run_automated_reconciliation
process_automation_event = _services.process_automation_event
AICopilotService = _services.AICopilotService
calculate_cash_transfer_amount = _services.calculate_cash_transfer_amount
validate_task3_cash_amount = _services.validate_task3_cash_amount
record_offline_event = _services.record_offline_event
validate_synced_registration = _services.validate_synced_registration


def run_deduplication_check(beneficiary):
    """Return the source detector's results in the existing API response shape."""

    results = _duplicates.DuplicateDetector.detect_for(beneficiary.id)
    signal_ids = list(dict.fromkeys(result["signal_id"] for result in results))
    signals_by_id = {
        str(signal.id): signal
        for signal in AISignal.objects.filter(id__in=signal_ids)
    }
    return {
        "matches": len(results),
        "signals": [
            signals_by_id[result["signal_id"]]
            for result in results
            if result["signal_id"] in signals_by_id
        ],
        "results": results,
    }


def verified_program_summary(program):
    """Keep the existing verified-metrics API separate from AI implementation."""

    payments = PaymentInstruction.objects.filter(
        Q(cash_entitlement__program=program) | Q(enrollment__program=program)
    )
    enrollments = Enrollment.objects.filter(program=program)
    return {
        "beneficiaries": Beneficiary.objects.filter(household__program=program).count(),
        "eligibility": list(
            enrollments.values("eligibility_status").order_by("eligibility_status")
        ),
        "approvals": enrollments.filter(status=Enrollment.Status.APPROVED).count(),
        "payments": list(payments.values("status").order_by("status")),
        "amounts": {
            "approved": str(
                (program.transfer_amount or 0)
                * enrollments.filter(status=Enrollment.Status.APPROVED).count()
            ),
            "distributed": str(
                sum(
                    (
                        payment.amount
                        for payment in payments.filter(
                            status=PaymentInstruction.Status.SUCCESS
                        )
                    ),
                    0,
                )
            ),
        },
        "failures": payments.filter(status=PaymentInstruction.Status.FAILED).count(),
        "complaints": Complaint.objects.filter(
            beneficiary__household__program=program
        ).count(),
        "verified_at": _services.timezone.now(),
    }


def import_data_command_class():
    """Load the source folder's Django management command on demand."""

    return _import_command.Command
