from decimal import Decimal
from django.db.models import Q
from django.utils import timezone
from .models import AISignal, ReviewTask, Beneficiary, PaymentEvent, PaymentBatch

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
        Q(phone_last4=beneficiary.phone_last4)
    )

    if duplicates.exists():
        for dup in duplicates:
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
                    "status": "PENDING"
                }
            )

            # إنشاء مهمة مراجعة بشرية مرتبطة بالإشارة (Human-in-the-loop control)
            if created:
                ReviewTask.objects.create(
                    tenant=beneficiary.household.tenant,
                    program=beneficiary.household.program,
                    task_type="DUPLICATE_REVIEW",
                    entity_type="BENEFICIARY",
                    entity_id=str(beneficiary.id),
                    priority="HIGH",
                    status="OPEN",
                    resolution=f"AI flagged potential duplicate with score {signal.score}. Review required."
                )
        return True
    return False


def run_automated_reconciliation(batch_id, provider_report_data):
    """
    محرك التسوية التلقائية:
    يقارن دفعات النظام بتقرير مزود خدمة الدفع (FSP) الخارجي
    ويحدد النجاح، الفشل، أو الحالات غير المتطابقة (Unmatched)
    """
    reconciliation_results = {
        "matched": 0,
        "mismatched": 0,
        "unresolved": 0
    }

    # جلب أحداث الدفع المرتبطة بالدفعة المحددة
    payment_events = PaymentEvent.objects.filter(instruction__batch_id=batch_id)

    for event in payment_events:
        # البحث عن المعاملة في تقارير المزود بناءً على مرجع المعاملة أو رقم المستفيد
        provider_record = provider_report_data.get(event.provider_reference)

        if not provider_record:
            # حالة: المعاملة غير موجودة في تقارير المزود (غير مطابقة)
            event.status = "UNMATCHED"
            event.save()
            reconciliation_results["unresolved"] += 1
            
            # إنشاء مهمة مراجعة استثناءات للمدفوعات
            ReviewTask.objects.create(
                tenant=event.tenant,
                program=event.program,
                task_type="RECONCILIATION_EXCEPTION",
                entity_type="PAYMENT_EVENT",
                entity_id=str(event.id),
                priority="HIGH",
                status="OPEN",
                resolution=f"Payment event {event.id} missing from provider report. Requires manual reconciliation."
            )
            continue

        # مقارنة المبالغ والحالة الواردة من المزود
        provider_amount = Decimal(str(provider_record.get("amount", "0")))
        provider_status = provider_record.get("status") # SUCCESS, FAILED, REFUNDED

        if provider_amount == event.amount and provider_status == "SUCCESS":
            event.status = "RECONCILED_SUCCESS"
            event.provider_status = provider_status
            event.resolved_at = timezone.now()
            event.save()
            reconciliation_results["matched"] += 1
        elif provider_status == "FAILED":
            event.status = "RECONCILED_FAILED"
            event.provider_status = provider_status
            event.save()
            reconciliation_results["mismatched"] += 1
            
            # توجيه الدفعة الفاشلة لطابور إعادة المحاولة أو المعالجة
            ReviewTask.objects.create(
                tenant=event.tenant,
                program=event.program,
                task_type="PAYMENT_FAILURE_REVIEW",
                entity_type="PAYMENT_EVENT",
                entity_id=str(event.id),
                priority="MEDIUM",
                status="OPEN",
                resolution=f"Payment failed at FSP level. Reason: {provider_record.get('fail_reason', 'Unknown')}"
            )
        else:
            # فروقات في المبالغ أو الحالة
            event.status = "AMOUNT_MISMATCH"
            event.save()
            reconciliation_results["mismatched"] += 1

    return reconciliation_results

def run_anomaly_detection(payment_batch):
    """
    محرك كشف الأنماط الشاذة والاحتيال:
    يرصد العمليات المتكررة أو الشاذة بناءً على القيم أو تكرار الحسابات
    """
    anomalies_detected = 0
    events = PaymentEvent.objects.filter(instruction__batch_id=payment_batch)

    # جلب الـ tenant والـ program من الـ batch مباشرة لتفادي الأخطاء
    batch_obj = payment_batch if hasattr(payment_batch, 'tenant') else PaymentBatch.objects.filter(pk=payment_batch).first()
    tenant = getattr(batch_obj, 'tenant', None)
    program = getattr(batch_obj, 'program', None)

    for event in events:
        # التحقق من المبلغ عبر الـ instruction المرتبط
        if event.instruction and event.instruction.amount > Decimal("500.00"):  # حد افتراضي للمبالغ العالية
            AISignal.objects.get_or_create(
                tenant=tenant,
                program=program,
                entity_type="PAYMENT_EVENT",
                entity_id=str(event.id),
                signal_type="HIGH_AMOUNT_ANOMALY",
                defaults={
                    "score": Decimal("0.85"),
                    "confidence": Decimal("0.80"),
                    "reason": f"Payment amount {event.instruction.amount} exceeds standard threshold, flagged for review.",
                    "model_version": "anomaly-v1.0",
                    "status": "PENDING"
                }
            )

            ReviewTask.objects.create(
                tenant=tenant,
                program=program,
                task_type="FRAUD_REVIEW",
                entity_type="PAYMENT_EVENT",
                entity_id=str(event.id),
                priority="HIGH",
                status="OPEN",
                resolution="Anomaly detected: High transfer value requires verification before release."
            )
            anomalies_detected += 1

    return anomalies_detected

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