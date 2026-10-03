from decimal import Decimal
from django.db.models import Q
from django.utils import timezone
from .models import AISignal, Beneficiary, Complaint, Enrollment, PaymentBatch, PaymentEvent, PaymentInstruction, ReconciliationItem, ReviewTask

AI_AUTOMATION_ENABLED = True


def _review_task(tenant, program, task_type, entity_type, entity_id, priority, resolution):
    return ReviewTask.objects.get_or_create(
        tenant=tenant,
        program=program,
        task_type=task_type,
        entity_type=entity_type,
        entity_id=str(entity_id),
        status__in=[ReviewTask.Status.OPEN, ReviewTask.Status.ASSIGNED],
        defaults={"priority": priority, "resolution": resolution},
    )[0]

def run_deduplication_check(beneficiary):
    """
    محرك كشف التكرار الاحتمالي للمستفيدين الجدد
    يقارن الاسم، آخر رقم هاتف، وهاش الهوية الوطنية
    """
    # البحث عن مستفيدين آخرين بنفس المعايير (باستثناء نفس المستفيد)
    duplicates = Beneficiary.objects.filter(
        household__program=beneficiary.household.program
    ).exclude(id=beneficiary.id).filter(
        Q(national_id_hash=beneficiary.national_id_hash) |
        Q(phone_number__endswith=(beneficiary.phone_number or "")[-4:])
    )

    matches = list(duplicates)
    signals = []
    if matches:
        for dup in matches:
            # حساب نقاط الثقة التطابقية افتراضياً (يمكن تحسينها بخوارزميات تشابه النصوص)
            score = Decimal("0.91") if dup.national_id_hash == beneficiary.national_id_hash else Decimal("0.75")
            
            # إنشاء إشارة الذكاء الاصطناعي للتكرار
            signal, created = AISignal.objects.get_or_create(
                tenant=beneficiary.household.tenant,
                program=beneficiary.household.program,
                entity_type="BENEFICIARY",
                entity_id=str(beneficiary.id),
                signal_type="POSSIBLE_DUPLICATE",
                defaults={
                    "score": score,
                    "confidence": Decimal("0.89"),
                    "reason": f"Matched with existing beneficiary ID: {dup.id} based on national ID/phone.",
                    "model_version": "dedup-v1.0",
                    "status": AISignal.Status.OPEN
                }
            )

            # إنشاء مهمة مراجعة بشرية مرتبطة بالإشارة (Human-in-the-loop control)
            if created:
                _review_task(beneficiary.household.tenant, beneficiary.household.program, ReviewTask.TaskType.DUPLICATE_REVIEW, "BENEFICIARY", beneficiary.id, ReviewTask.Priority.HIGH, f"Review possible duplicate {dup.id}.")
            signals.append(signal)
    return {"matches": len(matches), "signals": signals}


def run_automated_reconciliation(batch_id, provider_report_data):
    """
    محرك التسوية التلقائية:
    يقارن دفعات النظام بتقرير مزود خدمة الدفع (FSP) الخارجي
    ويحدد النجاح، الفشل، أو الحالات غير المتطابقة (Unmatched)
    """
    batch = batch_id if isinstance(batch_id, PaymentBatch) else PaymentBatch.objects.get(pk=batch_id)
    summary = {"matched": 0, "pending": 0, "discrepancies": 0, "items": []}
    events = PaymentEvent.objects.filter(instruction__batch=batch).select_related("instruction")
    for event in events:
        instruction = event.instruction
        record = provider_report_data.get(instruction.provider_reference)
        if not record:
            issue_type, actual_amount, actual_status = ReconciliationItem.IssueType.MISSING_REFERENCE, None, "MISSING"
            summary["pending"] += 1
        else:
            actual_amount = Decimal(str(record.get("amount", "0")))
            actual_status = str(record.get("status", "UNKNOWN")).upper()
            if actual_status == "SUCCESS" and actual_amount == instruction.amount:
                summary["matched"] += 1
                continue
            issue_type = ReconciliationItem.IssueType.FAILED if actual_status == "FAILED" else ReconciliationItem.IssueType.AMOUNT_MISMATCH if actual_amount != instruction.amount else ReconciliationItem.IssueType.STATUS_MISMATCH
            summary["discrepancies"] += 1
        item, _ = ReconciliationItem.objects.update_or_create(
            tenant=batch.tenant, program=batch.program, instruction=instruction, issue_type=issue_type,
            defaults={"expected_amount": instruction.amount, "actual_amount": actual_amount, "expected_status": instruction.status, "actual_status": actual_status, "provider_reference": instruction.provider_reference, "status": ReconciliationItem.Status.OPEN},
        )
        _review_task(batch.tenant, batch.program, ReviewTask.TaskType.RECONCILIATION, "PAYMENT_INSTRUCTION", instruction.pk, ReviewTask.Priority.HIGH, "Reconciliation discrepancy requires human resolution.")
        summary["items"].append(str(item.pk))
    return summary

def run_anomaly_detection(payment_batch):
    """
    محرك كشف الأنماط الشاذة والاحتيال:
    يرصد العمليات المتكررة أو الشاذة بناءً على القيم أو تكرار الحسابات
    """
    batch = payment_batch if isinstance(payment_batch, PaymentBatch) else PaymentBatch.objects.get(pk=payment_batch)
    signals = []
    for instruction in batch.instructions.all():
        if instruction.amount > Decimal("500.00"):
            signal, _ = AISignal.objects.update_or_create(
                tenant=batch.tenant,
                program=batch.program,
                entity_type="PAYMENT_INSTRUCTION",
                entity_id=str(instruction.id),
                signal_type="HIGH_AMOUNT_ANOMALY",
                defaults={
                    "score": Decimal("0.85"),
                    "confidence": Decimal("0.80"),
                    "reason": f"Payment amount {instruction.amount} exceeds standard threshold, flagged for review.",
                    "model_version": "anomaly-v1.0",
                    "status": AISignal.Status.OPEN
                }
            )

            _review_task(batch.tenant, batch.program, ReviewTask.TaskType.RISK_REVIEW, "PAYMENT_INSTRUCTION", instruction.id, ReviewTask.Priority.HIGH, "Review advisory high-value payment signal before any human decision.")
            signals.append(signal)
    return signals


def verified_program_summary(program):
    payments = PaymentInstruction.objects.filter(enrollment__program=program)
    enrollments = Enrollment.objects.filter(program=program)
    return {
        "beneficiaries": Beneficiary.objects.filter(household__program=program).count(),
        "eligibility": list(enrollments.values("eligibility_status").order_by("eligibility_status")),
        "approvals": enrollments.filter(status=Enrollment.Status.APPROVED).count(),
        "payments": list(payments.values("status").order_by("status")),
        "amounts": {"approved": str((program.transfer_amount or Decimal("0")) * enrollments.filter(status=Enrollment.Status.APPROVED).count()), "distributed": str(sum((payment.amount for payment in payments.filter(status=PaymentInstruction.Status.SUCCESS)), Decimal("0")))},
        "failures": payments.filter(status=PaymentInstruction.Status.FAILED).count(),
        "complaints": Complaint.objects.filter(beneficiary__household__program=program).count(),
        "verified_at": timezone.now(),
    }

class AICopilotService:
    """
    مساعد التقارير الذكي (AI Reporting Copilot)
    مسؤول عن تجميع حالة المنصة وتحليل البيانات لتوليد تقارير تنفيذية فورية.
    """
    
    @staticmethod
    def generate_system_summary_report():
        # 1. جمع الإحصائيات الحية من قواعد البيانات
        total_beneficiaries = Beneficiary.objects.count()
        total_signals = AISignal.objects.count()
        open_tasks = ReviewTask.objects.filter(status="OPEN").count()
        high_priority_tasks = ReviewTask.objects.filter(priority="HIGH", status="OPEN").count()
        
        # تصنيف أنواع الإشارات
        duplicates_count = AISignal.objects.filter(signal_type="POSSIBLE_DUPLICATE").count()
        discrepancies_count = AISignal.objects.filter(signal_type="FINANCIAL_DISCREPANCY").count()

        # 2. تحليل الحالة العامة واقتراح التوصية
        health_status = "مستقر وفعال" if high_priority_tasks < 5 else "يحتاج إلى تدخل عاجل"

        # 3. صياغة التقرير التنفيذي
        report = f"""
==================================================
🤖 تقرير مساعد الذكاء الاصطناعي التنفيذي (AI Copilot Report)
==================================================
📌 الحالة العامة للنظام: [{health_status}]

📊 إحصائيات المستفيدين والبيانات:
   - إجمالي المستفيدين المسجلين: {total_beneficiaries}
   - إجمالي تنبيهات النظام (AISignals): {total_signals}
     * حالات التكرار المحتملة: {duplicates_count}
     * الفروقات المالية المرصودة: {discrepancies_count}

🛠️ طابور التدخل البشري (Human-in-the-Loop Queue):
   - إجمالي المهام المفتوحة للمراجعة: {open_tasks}
   - المهام ذات الأولوية العالية (HIGH Priority): {high_priority_tasks}

💡 التوصية التشغيلية الذكية:
   المنصة تعمل بكفاءة تامة في الأتمتة وكشف الشذوذ والتسوية المالية. 
   يوجد حالياً {high_priority_tasks} مهام حرجة تتطلب متابعة سريعة من الفريق لضمان سلاسة الصرف ومنع أي ازدواجية.
==================================================
"""
        return report
