import { cloneElement, createContext, FormEvent, isValidElement, ReactNode, useContext, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import "bootstrap/dist/css/bootstrap.min.css";
import "./styles.css";
import { Banknote, BarChart3, Bot, ClipboardCheck, FileText, FileUp, Globe2, Languages, LayoutDashboard, MessageSquareWarning, ShieldCheck, UserCog, Users } from "lucide-react";
import { api, listItems, PlatformUser } from "./api";

type Role = "ADMIN" | "FIELD_OFFICER" | "FINANCE" | "REVIEWER" | "SUPPORT" | "MANAGER" | "AUDITOR";
type Route = "dashboard" | "tenants" | "programs" | "households" | "beneficiaries" | "eligibility" | "enrollment" | "payments" | "pdm" | "complaints" | "budgets" | "activities" | "audit" | "imports" | "automation" | "users" | "profile";
type Locale = "en" | "ar";
type Entity = { id: string; name?: string; full_name?: string; number?: string; status?: string; currency?: string; program?: string; household?: string; beneficiary?: string; enrollment?: string; channel_config?: string; tenant?: string; [key: string]: unknown };
type Option = { value: string; label: string };

const arabic: Record<string, string> = {
  "Dashboard": "لوحة المعلومات", "Platform administration": "إدارة المنصة", "Programs": "البرامج", "Households": "الأسر", "Beneficiaries": "المستفيدون", "Eligibility review": "مراجعة الأهلية", "Enrollment decisions": "قرارات التسجيل", "Payments": "المدفوعات", "PDM": "متابعة ما بعد التوزيع", "Complaints": "الشكاوى", "Budgets": "الميزانيات", "Program activities": "أنشطة البرنامج", "Audit": "سجل التدقيق", "Import records": "استيراد السجلات", "AI & Automation": "الذكاء الاصطناعي والأتمتة", "User administration": "إدارة المستخدمين",
  "Humanitarian operations": "العمليات الإنسانية", "H-CAP sign in": "تسجيل الدخول إلى H-CAP", "Sign in with your staff account to see only your permitted services.": "سجّل الدخول بحساب الموظف لرؤية الخدمات المصرح بها فقط.", "Work email": "البريد الإلكتروني للعمل", "Password": "كلمة المرور", "Signing in…": "جارٍ تسجيل الدخول…", "Sign in": "تسجيل الدخول", "Sign out": "تسجيل الخروج", "Skip to content": "تخطي إلى المحتوى", "Secure, accountable assistance delivery": "تقديم مساعدات آمنة وخاضعة للمساءلة", "Humanitarian Cash Assistance Platform": "منصة المساعدات النقدية الإنسانية", "Edit profile": "تعديل الملف الشخصي", "Profile settings": "إعدادات الملف الشخصي", "Update personal details": "تحديث البيانات الشخصية", "Change the name and email shown on your H-CAP account.": "غيّر الاسم والبريد الإلكتروني الظاهرين في حساب H-CAP.", "Current password": "كلمة المرور الحالية", "Save details": "حفظ البيانات", "Change password": "تغيير كلمة المرور", "Use your current password to set a new password for this account.": "استخدم كلمة المرور الحالية لتعيين كلمة مرور جديدة لهذا الحساب.", "New password": "كلمة المرور الجديدة", "Confirm new password": "تأكيد كلمة المرور الجديدة", "Update password": "تحديث كلمة المرور", "Use at least 8 characters.": "استخدم 8 أحرف على الأقل.", "How to use this function": "كيفية استخدام هذه الوظيفة", "Choose a beneficiary to compare with other beneficiaries in the same authorized program. The result can create an advisory signal and a human review task; it never deletes or approves records.": "اختر مستفيداً لمقارنته بالمستفيدين الآخرين في البرنامج المصرح به. قد تنشئ النتيجة إشارة استشارية ومهمة مراجعة بشرية، ولا تحذف أو تعتمد أي سجل.", "Choose a simulated payment batch. The scan creates advisory risk signals for human review and does not change payment status.": "اختر دفعة دفع محاكية. ينشئ الفحص إشارات مخاطر استشارية للمراجعة البشرية ولا يغير حالة الدفع.", "Choose a payment batch and enter the simulated provider report as JSON. Each reference needs an amount and status. The result identifies matched payments and exceptions for human resolution.": "اختر دفعة دفع وأدخل تقرير المزود المحاكي بصيغة JSON. يحتاج كل مرجع إلى مبلغ وحالة. تحدد النتيجة المدفوعات المتطابقة والاستثناءات للحل البشري.", "Choose a program and ask for verified beneficiaries, approvals, payments, failures, complaints, or budget figures. The result is a verified metrics draft for people to interpret; it does not make decisions.": "اختر برنامجاً واطلب أرقام المستفيدين أو الموافقات أو المدفوعات أو حالات الفشل أو الشكاوى أو الميزانية الموثقة. النتيجة مسودة مؤشرات موثقة للمراجعة البشرية ولا تتخذ قرارات.", "Choose an active rule. CREATE_REVIEW_TASK adds a human task to the queue. RETRY, ESCALATE, and REMINDER are recorded as dry runs only, so they do not change payment or eligibility.": "اختر قاعدة نشطة. يضيف CREATE_REVIEW_TASK مهمة بشرية إلى قائمة الانتظار. تسجل RETRY وESCALATE وREMINDER كتجارب جافة فقط، لذلك لا تغير الدفع أو الأهلية.", "Select an open signal created by duplicate or risk detection, choose the human outcome, and record the evidence or decision note. This closes the signal; it does not automatically change eligibility or payment.": "اختر إشارة مفتوحة أنشأها كشف التكرار أو المخاطر، وحدد النتيجة البشرية وسجل الدليل أو ملاحظة القرار. يغلق ذلك الإشارة ولا يغير الأهلية أو الدفع تلقائياً.", "Select an open task, review the related records, and write what was decided or what follow-up is required. Resolving the task records the human action in the audit history.": "اختر مهمة مفتوحة، وراجع السجلات المرتبطة، واكتب القرار أو المتابعة المطلوبة. يسجل حل المهمة الإجراء البشري في سجل التدقيق.", "Select an open exception, compare the expected and provider values, then record how the mismatch was resolved. The item remains an auditable human resolution.": "اختر استثناءً مفتوحاً، وقارن القيم المتوقعة بقيم المزود، ثم سجل كيفية حل الفرق. يبقى البند حلاً بشرياً قابلاً للتدقيق.",
  "No records found.": "لا توجد سجلات.", "Select…": "اختر…", "Saving. Do not submit again.": "جارٍ الحفظ. لا تُرسل الطلب مرة أخرى.", "Saved successfully.": "تم الحفظ بنجاح.", "Loading": "جارٍ التحميل", "Scope:": "النطاق:", "All tenants": "كل الجهات", "All programs": "كل البرامج", "Unnamed tenant": "جهة غير مسماة", "Unnamed program": "برنامج غير مسمى", "Program": "البرنامج", "Tenant": "الجهة", "Status": "الحالة", "Actions": "الإجراءات", "Result": "النتيجة", "Value": "القيمة", "Primary navigation": "التنقل الرئيسي",
  "Platform dashboard": "لوحة معلومات المنصة", "Operational dashboard": "لوحة المعلومات التشغيلية", "Platform dashboard: select all tenants, one tenant, or one program.": "لوحة معلومات المنصة: اختر كل الجهات أو جهة واحدة أو برنامجاً واحداً.", "Finance dashboard: simulated distribution, reconciliation, and budget figures for authorized programs.": "لوحة المالية: التوزيع المحاكى والتسوية والميزانية للبرامج المصرح بها.", "Tenant dashboard: operational figures for your organization and selected program.": "لوحة الجهة: المؤشرات التشغيلية لجهتك والبرنامج المختار.", "Field dashboard: registration and permitted follow-up work.": "لوحة الميدان: التسجيل وأعمال المتابعة المصرح بها.", "Support dashboard: complaints and PDM follow-up work.": "لوحة الدعم: الشكاوى ومتابعة ما بعد التوزيع.", "Reviewer dashboard: human eligibility and advisory review work.": "لوحة المراجع: الأهلية البشرية وأعمال المراجعة الاستشارية.", "Read-only tenant-scoped operational dashboard.": "لوحة تشغيلية للقراءة فقط ضمن نطاق الجهة.",
  "Tenants": "الجهات", "Type": "النوع", "Currency": "العملة", "Create tenant and first manager": "إنشاء جهة وأول مدير", "Tenant name": "اسم الجهة", "Tenant type": "نوع الجهة", "Default currency": "العملة الافتراضية", "First manager name": "اسم المدير الأول", "First manager email": "بريد المدير الأول", "Temporary password": "كلمة مرور مؤقتة", "Create tenant": "إنشاء الجهة",
  "Create program": "إنشاء برنامج", "Program name": "اسم البرنامج", "Country": "الدولة", "Country code": "رمز الدولة", "Program currency": "عملة البرنامج", "Reporting currency": "عملة التقارير", "Transfer amount": "مبلغ التحويل", "Payment cycle": "دورة الدفع", "Budget envelope": "سقف الميزانية", "Enable simulated payments for this program": "تمكين المدفوعات المحاكية لهذا البرنامج", "Create program and budget": "إنشاء البرنامج والميزانية", "Configure simulated payment channel": "تهيئة قناة دفع محاكية", "Channel type": "نوع القناة", "Simulated provider name": "اسم المزود المحاكي", "Channel currency": "عملة القناة", "Save simulated channel": "حفظ القناة المحاكية", "Finance can configure only simulated channels. Program creation remains with tenant administrators and managers.": "يمكن للمالية تهيئة القنوات المحاكية فقط. إنشاء البرامج خاص بمديري الجهات.", "Authorized programs": "البرامج المصرح بها",
  "Register household": "تسجيل أسرة", "Register beneficiary": "تسجيل مستفيد", "Household": "الأسرة", "National ID": "الرقم الوطني", "Full name": "الاسم الكامل", "Gender": "النوع", "Female": "أنثى", "Male": "ذكر", "Date of birth": "تاريخ الميلاد", "Phone number": "رقم الهاتف", "Consent given": "تم الحصول على الموافقة", "Verification status": "حالة التحقق", "Household size": "حجم الأسرة", "Location": "الموقع", "Registration date": "تاريخ التسجيل", "Save beneficiary": "حفظ المستفيد", "Save household": "حفظ الأسرة", "Registration reference": "مرجع التسجيل", "System reference": "مرجع النظام", "Not recorded": "غير مسجل", "Verification": "التحقق", "Add beneficiary to this household": "إضافة مستفيد إلى هذه الأسرة", "Household saved:": "تم حفظ الأسرة:",
  "Eligibility and advisory work": "أعمال الأهلية والاستشارة", "Eligibility": "الأهلية", "Eligibility status": "حالة الأهلية", "Enrollment status": "حالة التسجيل", "Create human review": "إنشاء مراجعة بشرية", "Open advisory signals": "إشارات استشارية مفتوحة", "Human review tasks": "مهام المراجعة البشرية", "Enrollment decision": "قرار التسجيل", "Enrollment": "التسجيل", "Decision": "القرار", "Record human decision": "تسجيل القرار البشري", "Enrollment history": "سجل التسجيل",
  "Create simulated payment batch": "إنشاء دفعة دفع محاكية", "Batch name": "اسم الدفعة", "Create simulated batch": "إنشاء دفعة محاكية", "Create simulated payment instruction": "إنشاء تعليمات دفع محاكية", "Approved enrollment": "تسجيل معتمد", "Simulated payment channel": "قناة دفع محاكية", "Payment batch": "دفعة الدفع", "Amount": "المبلغ", "This uses the built-in simulator only. No external payment provider is called.": "يستخدم هذا المحاكي المدمج فقط. لا يتم الاتصال بأي مزود دفع خارجي.", "Create simulated instruction": "إنشاء تعليمات محاكية", "Simulated payment instructions": "تعليمات الدفع المحاكية", "Beneficiary": "المستفيد", "Channel": "القناة", "Events": "الأحداث", "Submit": "إرسال", "Success": "نجاح", "Fail": "فشل", "Reverse": "عكس", "Append-only payment events": "أحداث الدفع غير القابلة للتعديل", "Event": "الحدث", "Provider status": "حالة المزود", "From": "من", "To": "إلى", "Time": "الوقت",
  "Register complaint": "تسجيل شكوى", "Beneficiary / household": "المستفيد / الأسرة", "Category": "الفئة", "Description": "الوصف", "Severity": "الخطورة", "Assign, investigate, or resolve": "تعيين الشكوى والتحقيق فيها أو حلها", "Open complaint": "شكوى مفتوحة", "Assign to tenant user": "تعيين لمستخدم في الجهة", "Case status": "حالة القضية", "Investigation or resolution note": "ملاحظة التحقيق أو الحل", "Save complaint update": "حفظ تحديث الشكوى", "Assigned to": "مُسندة إلى", "Resolution": "الحل", "Unassigned": "غير مسندة",
  "Manage program budget": "إدارة ميزانية البرنامج", "Budget planned total": "إجمالي الميزانية المخطط", "Budget actual total": "إجمالي الميزانية الفعلي", "Budget status": "حالة الميزانية", "Save budget": "حفظ الميزانية", "Budget summaries": "ملخصات الميزانية", "Planned": "المخطط", "Actual": "الفعلي", "Remaining": "المتبقي",
  "Post-Distribution Monitoring": "متابعة ما بعد التوزيع", "Received rate": "نسبة الاستلام", "Amount received": "المبلغ المستلم", "Access problem rate": "نسبة مشكلات الوصول", "Complaint rate": "نسبة الشكاوى", "Satisfaction": "الرضا", "Submit PDM response": "إرسال استجابة متابعة ما بعد التوزيع", "PDM summary": "ملخص متابعة ما بعد التوزيع", "View calculated summary": "عرض الملخص المحسوب", "Selected program": "البرنامج المختار", "Responses": "الاستجابات", "Access problems": "مشكلات الوصول",
  "Controlled CSV/XLSX import": "استيراد CSV/XLSX مضبوط", "Required columns: client_generated_id, household_size, location, national_id (or beneficiary_number), full_name. The import file has its own client reference column; online household registration does not require it.": "الأعمدة المطلوبة: معرف العميل وحجم الأسرة والموقع والرقم الوطني أو رقم المستفيد والاسم الكامل. ملف الاستيراد له مرجع عميل خاص به؛ التسجيل الإلكتروني للأسرة لا يتطلبه.", "CSV or XLSX file": "ملف CSV أو XLSX", "Preview and validate": "معاينة والتحقق", "Confirm and save valid rows": "تأكيد وحفظ الصفوف الصحيحة", "Row": "الصف", "Field": "الحقل", "Message": "الرسالة",
  "Add program activity": "إضافة نشاط للبرنامج", "Activity name": "اسم النشاط", "Workstream": "مسار العمل", "Owner": "المسؤول", "Progress (%)": "التقدم (%)", "Risk": "المخاطر", "Save activity": "حفظ النشاط", "Add finish-to-start dependency": "إضافة تبعية إنهاء إلى بدء", "Predecessor activity": "النشاط السابق", "Successor activity": "النشاط اللاحق", "Lag days": "أيام التأخير", "Reason": "السبب", "Save dependency": "حفظ التبعية", "Precedence diagram activity list": "قائمة مخطط أسبقية الأنشطة", "Dependencies": "التبعيات",
  "AI and automation status": "حالة الذكاء الاصطناعي والأتمتة", "Advisory AI signals:": "إشارات الذكاء الاصطناعي الاستشارية:", "Open human reviews:": "المراجعات البشرية المفتوحة:", "Configured event": "الحدث المهيأ", "Controlled action": "الإجراء المضبوط", "Priority": "الأولوية", "Duplicate review": "مراجعة التكرار", "The existing detector creates advisory signals and human review tasks only.": "ينشئ الكاشف الحالي إشارات استشارية ومهام مراجعة بشرية فقط.", "Run duplicate check": "تشغيل فحص التكرار", "Advisory risk review": "مراجعة المخاطر الاستشارية", "Simulated payment batch": "دفعة دفع محاكية", "Run advisory risk scan": "تشغيل فحص المخاطر الاستشاري", "Simulated reconciliation": "تسوية محاكية", "Controlled simulated provider report": "تقرير مزود محاكي مضبوط", "Reconcile simulated batch": "تسوية الدفعة المحاكية", "AI reporting copilot": "مساعد تقارير الذكاء الاصطناعي", "Reporting question": "سؤال التقرير", "Get verified draft": "الحصول على مسودة موثقة", "Execute controlled automation rule": "تنفيذ قاعدة أتمتة مضبوطة", "Active rule": "القاعدة النشطة", "Human review type": "نوع المراجعة البشرية", "Only review-task rules create work. Decision-sensitive actions remain dry runs for human approval.": "فقط قواعد مهام المراجعة تنشئ عملاً. الإجراءات الحساسة للقرار تبقى تجارب جافة للموافقة البشرية.", "Execute controlled rule": "تنفيذ القاعدة المضبوطة", "Record advisory signal review": "تسجيل مراجعة الإشارة الاستشارية", "Open advisory signal": "إشارة استشارية مفتوحة", "Human review outcome": "نتيجة المراجعة البشرية", "Review note": "ملاحظة المراجعة", "Save human review": "حفظ المراجعة البشرية", "Resolve review task": "حل مهمة مراجعة", "Open review task": "مهمة مراجعة مفتوحة", "Resolution note": "ملاحظة الحل", "Resolve task": "حل المهمة", "Resolve reconciliation exception": "حل استثناء التسوية", "Open reconciliation exception": "استثناء تسوية مفتوح", "Resolve reconciliation item": "حل بند التسوية", "Human review queue": "قائمة انتظار المراجعة البشرية", "Advisory AI signals": "إشارات الذكاء الاصطناعي الاستشارية", "Reconciliation exceptions": "استثناءات التسوية", "Issue": "المشكلة", "Expected": "المتوقع", "Pending human review": "بانتظار مراجعة بشرية", "Controlled service result": "نتيجة الخدمة المضبوطة", "Data pipeline status": "حالة مسار البيانات", "Pipeline stage": "مرحلة المسار", "beneficiary records, ": "سجلات المستفيدين، ", " advisory signals, and ": " إشارات استشارية، و", " review tasks are in the permitted scope.": " مهام مراجعة ضمن النطاق المصرح به.", "validation": "التحقق", "deduplication": "إزالة التكرار", "risk signals": "إشارات المخاطر", "human review": "المراجعة البشرية", "verified reporting": "التقارير الموثقة", "completed": "مكتمل",
  "All tenant users": "كل مستخدمي الجهات", "Tenant users": "مستخدمو الجهة", "Name": "الاسم", "Email": "البريد الإلكتروني", "Access": "الوصول", "Deactivate": "إيقاف", "Activate": "تفعيل", "Add tenant user": "إضافة مستخدم للجهة", "Role": "الدور", "Create user": "إنشاء المستخدم", "Audit history": "سجل التدقيق", "Action": "الإجراء", "Entity": "الكيان", "Actor": "المنفذ",
  "ACTIVE": "نشط", "INACTIVE": "غير نشط", "DRAFT": "مسودة", "OPEN": "مفتوح", "ASSIGNED": "مُسند", "RESOLVED": "محلول", "DISMISSED": "مرفوض", "PENDING": "قيد الانتظار", "SUBMITTED": "مُرسل", "CREATED": "تم الإنشاء", "SUCCESS": "ناجح", "FAILED": "فشل", "REVERSED": "معكوس", "REFUNDED": "مسترد", "RETRY": "إعادة محاولة", "EXCEPTION": "استثناء", "ELIGIBLE": "مؤهل", "INELIGIBLE": "غير مؤهل", "ON_HOLD": "معلّق", "ENROLLED": "مسجل", "APPROVED": "معتمد", "REJECTED": "مرفوض", "REGISTERED": "مسجل", "COMPLETED": "مكتمل", "BLOCKED": "محجوب", "NOT_STARTED": "لم يبدأ", "IN_PROGRESS": "قيد التنفيذ", "CLOSED": "مغلق", "LOW": "منخفض", "MEDIUM": "متوسط", "HIGH": "مرتفع", "CRITICAL": "حرج", "BANK": "بنك", "MOBILE_MONEY": "نقود عبر الهاتف", "CASH": "نقد", "VOUCHER": "قسيمة", "ONE_TIME": "مرة واحدة", "MONTHLY": "شهري", "QUARTERLY": "ربع سنوي", "FIELD_OFFICER": "موظف ميداني", "FINANCE": "المالية", "REVIEWER": "مراجع", "SUPPORT": "الدعم", "MANAGER": "مدير", "AUDITOR": "مدقق", "ADMIN": "مسؤول", "DATA_QUALITY": "جودة البيانات", "DUPLICATE_REVIEW": "مراجعة تكرار", "RISK_REVIEW": "مراجعة مخاطر", "ELIGIBILITY_REVIEW": "مراجعة الأهلية", "PAYMENT_EXCEPTION": "استثناء دفع", "RECONCILIATION": "تسوية", "COMPLAINT_ESCALATION": "تصعيد شكوى", "POSSIBLE_DUPLICATE": "تكرار محتمل", "HIGH_AMOUNT_ANOMALY": "شذوذ مبلغ مرتفع",
  "beneficiaries": "المستفيدون", "approvals": "الموافقات", "paid": "المدفوع", "pending": "قيد الانتظار", "failed": "الفاشل", "amounts approved": "المبالغ المعتمدة", "amounts distributed": "المبالغ الموزعة", "budget planned": "الميزانية المخططة", "budget actual": "الميزانية الفعلية", "budget remaining": "الميزانية المتبقية", "operational exceptions": "الاستثناءات التشغيلية",
};

const arabicInline: Record<string, string> = {
  "Update eligibility status": "تحديث حالة الأهلية", "New eligibility status": "حالة الأهلية الجديدة", "Save eligibility status": "حفظ حالة الأهلية", "Select an existing enrollment to save a new eligibility status.": "اختر تسجيلاً قائماً لحفظ حالة أهلية جديدة.",
  "Beneficiary": "المستفيد", "beneficiary": "المستفيد", "Household": "الأسرة", "household": "الأسرة", "Program": "البرنامج", "program": "البرنامج", "Tenant": "الجهة", "tenant": "الجهة", "Enrollment": "التسجيل", "enrollment": "التسجيل",
  "Country": "الدولة", "Currency": "العملة", "Amount": "المبلغ", "Category": "الفئة", "Description": "الوصف", "Severity": "الخطورة", "Priority": "الأولوية", "Status": "الحالة", "Actions": "الإجراءات", "Action": "الإجراء", "Reason": "السبب", "Resolution": "الحل", "Location": "الموقع", "Channel": "القناة", "Owner": "المسؤول", "Type": "النوع", "Name": "الاسم", "Email": "البريد الإلكتروني", "Role": "الدور", "Access": "الوصول", "Risk": "المخاطر", "Progress": "التقدم", "Issue": "المشكلة", "Expected": "المتوقع", "Actual": "الفعلي", "Time": "الوقت", "Events": "الأحداث",
  "Save": "حفظ", "Create": "إنشاء", "Delete": "حذف", "Edit": "تعديل", "Update": "تحديث", "View": "عرض", "Search": "بحث", "Assign": "تعيين", "Resolve": "حل", "Submit": "إرسال", "Close": "إغلاق", "Back": "رجوع", "Continue": "متابعة", "Cancel": "إلغاء", "Confirm": "تأكيد", "Yes": "نعم", "No": "لا", "Start": "يبدأ", "In progress": "قيد التنفيذ", "Finish": "ينتهي", "Blocked": "محجوب",
  "Not recorded": "غير مسجل", "Not available": "غير متاح", "Unassigned": "غير مسند", "Loading": "جارٍ التحميل", "System": "النظام", "Current tenant": "الجهة الحالية", "Current user": "المستخدم الحالي", "All programs": "كل البرامج", "All tenants": "كل الجهات", "Simulated batch": "دفعة محاكية", "Simulated channel": "قناة محاكية", "Household saved:": "تم حفظ الأسرة:",
  "Eligibility": "الأهلية", "Eligibility status": "حالة الأهلية", "Enrollment status": "حالة التسجيل", "Decision": "القرار", "Verification": "التحقق", "Verification status": "حالة التحقق", "National ID": "الرقم الوطني", "Phone number": "رقم الهاتف", "Full name": "الاسم الكامل", "Gender": "النوع", "Date of birth": "تاريخ الميلاد", "Registration date": "تاريخ التسجيل", "Registration reference": "مرجع التسجيل", "Household size": "حجم الأسرة", "Work email": "البريد الإلكتروني للعمل", "Temporary password": "كلمة مرور مؤقتة", "Current password": "كلمة المرور الحالية", "New password": "كلمة المرور الجديدة", "Confirm new password": "تأكيد كلمة المرور الجديدة",
  "Create human review": "إنشاء مراجعة بشرية", "Record human decision": "تسجيل القرار البشري", "Update personal details": "تحديث البيانات الشخصية", "Change password": "تغيير كلمة المرور", "Save details": "حفظ البيانات", "Update password": "تحديث كلمة المرور", "Edit profile": "تعديل الملف الشخصي", "User administration": "إدارة المستخدمين", "Audit history": "سجل التدقيق", "Assigned complaints": "الشكاوى المسندة", "Pending follow-up": "بانتظار المتابعة",
  "Payment batch": "دفعة الدفع", "Payment batches": "دفعات الدفع", "Payment instruction": "تعليمات الدفع", "Payment instructions": "تعليمات الدفع", "Simulated payment batch": "دفعة دفع محاكية", "Simulated payment channel": "قناة دفع محاكية", "Simulated payment instructions": "تعليمات الدفع المحاكية", "Create simulated payment batch": "إنشاء دفعة دفع محاكية", "Create simulated payment instruction": "إنشاء تعليمات دفع محاكية", "Create simulated batch": "إنشاء دفعة محاكية", "Create simulated instruction": "إنشاء تعليمات محاكية", "Append-only payment events": "أحداث الدفع غير القابلة للتعديل", "Provider status": "حالة المزود", "Provider report": "تقرير المزود", "Reconciliation exceptions": "استثناءات التسوية", "Resolve reconciliation exception": "حل استثناء التسوية",
  "AI and automation status": "حالة الذكاء الاصطناعي والأتمتة", "Configure automation rule": "تهيئة قاعدة أتمتة", "Execute controlled automation rule": "تنفيذ قاعدة أتمتة مضبوطة", "Controlled action": "الإجراء المضبوط", "Configured event": "الحدث المهيأ", "Active rule": "القاعدة النشطة", "Human review type": "نوع المراجعة البشرية", "Human review queue": "قائمة انتظار المراجعة البشرية", "Open human reviews:": "المراجعات البشرية المفتوحة:", "Record advisory signal review": "تسجيل مراجعة الإشارة الاستشارية", "Resolve review task": "حل مهمة مراجعة", "Review note": "ملاحظة المراجعة", "Resolution note": "ملاحظة الحل", "AI reporting copilot": "مساعد تقارير الذكاء الاصطناعي", "Reporting question": "سؤال التقرير", "Data pipeline status": "حالة مسار البيانات", "Pipeline stage": "مرحلة المسار",
  "CREATE_REVIEW_TASK": "إنشاء مهمة مراجعة", "PAYMENT_FAILED": "فشل الدفع", "DUPLICATE_FLAG": "إشارة تكرار", "HIGH_SEVERITY_COMPLAINT": "شكوى عالية الخطورة", "STALLED_REVIEW": "مراجعة متوقفة", "REMINDER": "تذكير", "ESCALATE": "تصعيد", "DRY_RUN": "تجربة جافة", "DRY_RUN_NOT_EXECUTED": "تجربة جافة لم تُنفذ", "HUMAN_REVIEW_TASK_CREATED": "تم إنشاء مهمة مراجعة بشرية", "IN_REVIEW": "قيد المراجعة", "ON_HOLD": "معلق", "POSSIBLE_DUPLICATE": "تكرار محتمل", "HIGH_AMOUNT_ANOMALY": "شذوذ في مبلغ مرتفع", "AMOUNT_MISMATCH": "فرق في المبلغ", "STATUS_MISMATCH": "فرق في الحالة", "MISSING_REFERENCE": "مرجع مفقود", "DATA_QUALITY": "جودة البيانات", "DUPLICATE_REVIEW": "مراجعة تكرار", "RISK_REVIEW": "مراجعة مخاطر", "ELIGIBILITY_REVIEW": "مراجعة الأهلية", "PAYMENT_EXCEPTION": "استثناء دفع", "COMPLAINT_ESCALATION": "تصعيد شكوى",
  "LOW": "منخفض", "MEDIUM": "متوسط", "HIGH": "مرتفع", "CRITICAL": "حرج", "PENDING": "قيد الانتظار", "ELIGIBLE": "مؤهل", "INELIGIBLE": "غير مؤهل", "APPROVED": "معتمد", "REJECTED": "مرفوض", "ENROLLED": "مسجل", "REGISTERED": "مسجل", "VERIFIED": "تم التحقق", "ACTIVE": "نشط", "INACTIVE": "غير نشط", "OPEN": "مفتوح", "ASSIGNED": "مسند", "RESOLVED": "محلول", "DISMISSED": "مرفوض", "DRAFT": "مسودة", "SUBMITTED": "مرسل", "CREATED": "تم الإنشاء", "SUCCESS": "ناجح", "FAILED": "فشل", "REVERSED": "معكوس", "REFUNDED": "مسترد", "BLOCKED": "محجوب", "COMPLETED": "مكتمل", "NOT_STARTED": "لم يبدأ", "IN_PROGRESS": "قيد التنفيذ",
  "FIELD_OFFICER": "موظف ميداني", "FINANCE": "المالية", "REVIEWER": "مراجع", "SUPPORT": "الدعم", "MANAGER": "مدير", "AUDITOR": "مدقق", "ADMIN": "مسؤول", "BANK": "بنك", "MOBILE_MONEY": "نقود عبر الهاتف", "CASH": "نقد", "VOUCHER": "قسيمة", "ONE_TIME": "مرة واحدة", "MONTHLY": "شهري", "QUARTERLY": "ربع سنوي", "FS": "إنهاء إلى بدء", "Finish to start": "إنهاء إلى بدء",
  "Could not load": "تعذر تحميل", "Could not save": "تعذر الحفظ", "Could not create": "تعذر الإنشاء", "Could not update": "تعذر التحديث", "Could not delete": "تعذر الحذف", "Request failed": "فشل الطلب", "Email or password is incorrect.": "البريد الإلكتروني أو كلمة المرور غير صحيحة.", "Unable to reach H-CAP. Check that the backend is running and try again.": "تعذر الاتصال بـ H-CAP. تأكد من أن الخادم يعمل ثم حاول مرة أخرى.",
};

function translateArabic(value: string) {
  const direct = arabic[value] ?? arabicInline[value];
  if (direct) return direct;
  return Object.entries(arabicInline)
    .sort(([first], [second]) => second.length - first.length)
    .reduce((localized, [source, replacement]) => localized.replaceAll(source, replacement), value);
}

const TranslationContext = createContext<(value: string) => string>((value) => value);
const useTranslation = () => useContext(TranslationContext);

function TranslateTree({ children }: { children: ReactNode }) {
  const translate = useTranslation();
  const localize = (node: ReactNode): ReactNode => {
    if (typeof node === "string") return translate(node);
    if (Array.isArray(node)) return node.map((item) => localize(item));
    if (isValidElement<{ children?: ReactNode }>(node) && node.props.children !== undefined) {
      return cloneElement(node, undefined, localize(node.props.children));
    }
    return node;
  };
  return <>{localize(children)}</>;
}

const roleRoutes: Record<Route, Role[]> = {
  dashboard: ["ADMIN", "FIELD_OFFICER", "FINANCE", "REVIEWER", "SUPPORT", "MANAGER", "AUDITOR"],
  tenants: ["ADMIN"],
  programs: ["ADMIN", "MANAGER", "FINANCE"],
  households: ["ADMIN", "FIELD_OFFICER", "MANAGER"],
  beneficiaries: ["ADMIN", "FIELD_OFFICER", "REVIEWER", "MANAGER"],
  eligibility: ["ADMIN", "REVIEWER", "MANAGER"],
  enrollment: ["ADMIN", "REVIEWER", "MANAGER"],
  payments: ["ADMIN", "FINANCE", "MANAGER", "AUDITOR"],
  pdm: ["ADMIN", "SUPPORT", "FIELD_OFFICER", "MANAGER", "AUDITOR"],
  complaints: ["ADMIN", "SUPPORT", "MANAGER"],
  budgets: ["ADMIN", "FINANCE", "MANAGER", "AUDITOR"],
  activities: ["ADMIN", "MANAGER", "FIELD_OFFICER", "REVIEWER", "FINANCE", "SUPPORT", "AUDITOR"],
  audit: ["ADMIN", "MANAGER", "AUDITOR"],
  imports: ["ADMIN", "FIELD_OFFICER", "MANAGER"],
  automation: ["ADMIN", "MANAGER", "REVIEWER", "FINANCE", "AUDITOR"],
  users: ["ADMIN", "MANAGER"],
  profile: ["ADMIN", "FIELD_OFFICER", "FINANCE", "REVIEWER", "SUPPORT", "MANAGER", "AUDITOR"],
};

const nav: Array<[Route, string, typeof LayoutDashboard]> = [
  ["dashboard", "Dashboard", LayoutDashboard],
  ["tenants", "Platform administration", Globe2],
  ["programs", "Programs", Globe2],
  ["households", "Households", Users],
  ["beneficiaries", "Beneficiaries", Users],
  ["eligibility", "Eligibility review", ClipboardCheck],
  ["enrollment", "Enrollment decisions", ClipboardCheck],
  ["payments", "Payments", Banknote],
  ["pdm", "PDM", BarChart3],
  ["complaints", "Complaints", MessageSquareWarning],
  ["budgets", "Budgets", FileText],
  ["activities", "Program activities", ClipboardCheck],
  ["audit", "Audit", ShieldCheck],
  ["imports", "Import records", FileUp],
  ["automation", "AI & Automation", Bot],
  ["users", "User administration", UserCog],
];

const routePaths: Record<Route, string> = {
  dashboard: "dashboard", tenants: "tenants", programs: "programs", households: "intake/households", beneficiaries: "intake/beneficiaries", eligibility: "eligibility", enrollment: "enrollment", payments: "payments", pdm: "pdm", complaints: "complaints", budgets: "budgets", activities: "activities", audit: "audit", imports: "imports", automation: "automation", users: "users", profile: "profile",
};

const routeFromHash = (): Route => (Object.entries(routePaths).find(([, path]) => path === location.hash.replace("#/", ""))?.[0] as Route) ?? "dashboard";
const valueOf = (record: Entity, key: string) => {
  const value = String(record[key] ?? "");
  return key === "relationship_type" && value === "FS" ? "Finish to start" : value;
};
const programLabel = (program: Entity) => program.name || "Unnamed program";
const householdLabel = (household: Entity) => `${valueOf(household, "registration_reference") || "Household"} — ${valueOf(household, "program_name") || "Program"} — ${valueOf(household, "location") || "Location not recorded"} — size ${valueOf(household, "household_size") || "?"}`;
const beneficiaryLabel = (beneficiary: Entity) => `${valueOf(beneficiary, "national_id_reference") || "National ID"} — ${beneficiary.full_name || "Unnamed"}`;
const enrollmentLabel = (enrollment: Entity) => `${valueOf(enrollment, "beneficiary_number") || "Beneficiary"} — ${valueOf(enrollment, "beneficiary_name") || "Unnamed"} (${valueOf(enrollment, "program_name") || "Program"})`;
const readableCode = (value: unknown) => String(value ?? "").replaceAll("_", " ").toLowerCase().replace(/\b\w/g, (character) => character.toUpperCase());

function Badge({ value }: { value: string }) {
  const translate = useTranslation();
  const tone = /FAILED|HIGH|CRITICAL|REJECTED/.test(value) ? "danger" : /PENDING|SUBMITTED|DRAFT|OPEN|HOLD/.test(value) ? "warning" : /ACTIVE|SUCCESS|VERIFIED|APPROVED|ELIGIBLE|RESOLVED|COMPLETED/.test(value) ? "success" : "secondary";
  return <span className={`badge text-bg-${tone}`}>{translate(value)}</span>;
}

function Panel({ title, children, action }: { title: string; children: ReactNode; action?: ReactNode }) {
  const translate = useTranslation();
  return <section className="panel"><div className="panel-title"><h2>{translate(title)}</h2>{action}</div><TranslateTree>{children}</TranslateTree></section>;
}

function FunctionHelp({ children }: { children: ReactNode }) {
  const translate = useTranslation();
  return <details className="function-help"><summary>{translate("How to use this function")}</summary><div className="function-help-body">{children}</div></details>;
}

function Table({ headings, rows }: { headings: string[]; rows: ReactNode[][] }) {
  const translate = useTranslation();
  return <div className="table-responsive"><table className="table align-middle"><thead><tr>{headings.map((heading) => <th scope="col" key={heading}>{translate(heading)}</th>)}</tr></thead><tbody>{rows.length ? rows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex}>{typeof cell === "string" ? translate(cell) : cell}</td>)}</tr>) : <tr><td colSpan={headings.length}>{translate("No records found.")}</td></tr>}</tbody></table></div>;
}

function Message({ status, error }: { status: string; error: string }) {
  const translate = useTranslation();
  if (!status && !error) return null;
  if (error) return <div className="alert alert-danger mt-3" role="alert">{translate(error)}</div>;
  return <div className="status-message mt-3" aria-live="polite">{status === "saving" ? translate("Saving. Do not submit again.") : status === "saved" ? translate("Saved successfully.") : ""}</div>;
}

function Field({ label, name, type = "text", required = false, help, defaultValue }: { label: string; name: string; type?: string; required?: boolean; help?: string; defaultValue?: string }) {
  const translate = useTranslation();
  const id = `field-${name}`;
  return <div className="mb-3"><label className="form-label" htmlFor={id}>{translate(label)}{required && " *"}</label><input className="form-control" id={id} name={name} type={type} required={required} defaultValue={defaultValue} />{help && <div className="form-text">{translate(help)}</div>}</div>;
}

function SelectField({ label, name, options, required = false, value, onChange, disabled = false }: { label: string; name: string; options: Option[]; required?: boolean; value?: string; onChange?: (value: string) => void; disabled?: boolean }) {
  const translate = useTranslation();
  const id = `field-${name}`;
  return <div className="mb-3"><label className="form-label" htmlFor={id}>{translate(label)}{required && " *"}</label><select className="form-select" id={id} name={name} required={required} value={value} onChange={(event) => onChange?.(event.target.value)} disabled={disabled}><option value="">{translate("Select…")}</option>{options.map((option) => <option key={option.value} value={option.value}>{translate(option.label)}</option>)}</select></div>;
}

function Textarea({ label, name, required = false, help, placeholder }: { label: string; name: string; required?: boolean; help?: string; placeholder?: string }) {
  const translate = useTranslation();
  const id = `field-${name}`;
  return <div className="mb-3"><label className="form-label" htmlFor={id}>{translate(label)}{required && " *"}</label><textarea className="form-control" id={id} name={name} required={required} rows={3} placeholder={placeholder ? translate(placeholder) : undefined} />{help && <div className="form-text">{translate(help)}</div>}</div>;
}

function Login({ onLogin }: { onLogin: (user: PlatformUser) => void }) {
  const translate = useTranslation();
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setSaving(true); setError("");
    try { onLogin(await api.login(String(form.get("email")), String(form.get("password")))); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Email or password is incorrect."); }
    finally { setSaving(false); }
  };
  return <TranslateTree><main className="login-page"><form className="login-card" onSubmit={submit}><div className="agency-mark" aria-hidden="true">+</div><p className="eyebrow">Humanitarian operations</p><h1>H-CAP sign in</h1><p>Sign in with your staff account to see only your permitted services.</p><Field label="Work email" name="email" type="email" required /><Field label="Password" name="password" type="password" required /><button className="btn btn-primary w-100" disabled={saving}>{saving ? translate("Signing in…") : translate("Sign in")}</button>{error && <div className="alert alert-danger mt-3" role="alert">{translate(error)}</div>}</form></main></TranslateTree>;
}

function Dashboard({ user }: { user: PlatformUser }) {
  const [tenants, setTenants] = useState<Entity[]>([]);
  const [programs, setPrograms] = useState<Entity[]>([]);
  const [tenantId, setTenantId] = useState("");
  const [programId, setProgramId] = useState("");
  const [summary, setSummary] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState("");
  const isPlatform = user.is_superuser;

  useEffect(() => {
    const load = async () => {
      try {
        const [programRecords, tenantRecords] = await Promise.all([
          api.list<Entity>("programs"),
          isPlatform ? api.list<Entity>("tenants") : Promise.resolve([] as Entity[]),
        ]);
        setPrograms(listItems(programRecords));
        setTenants(isPlatform ? listItems(tenantRecords as Entity[] | { results: Entity[] }) : []);
      } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not load dashboard scope."); }
    };
    void load();
  }, [isPlatform]);

  const visiblePrograms = useMemo(() => tenantId ? programs.filter((program) => valueOf(program, "tenant") === tenantId) : programs, [programs, tenantId]);
  useEffect(() => { if (programId && !visiblePrograms.some((program) => program.id === programId)) setProgramId(""); }, [programId, visiblePrograms]);
  useEffect(() => {
    const load = async () => {
      const query = new URLSearchParams();
      if (tenantId) query.set("tenant", tenantId);
      if (programId) query.set("program", programId);
      try { setSummary(await api.get<{ dashboards: Record<string, unknown>; scope: Record<string, unknown> }>(`/reports/${query.toString() ? `?${query}` : ""}`).then((result) => ({ ...result.dashboards, scope: result.scope }))); }
      catch (reason) { setError(reason instanceof Error ? reason.message : "Could not load dashboard data."); }
    };
    void load();
  }, [tenantId, programId]);

  const scope = (summary?.scope as Record<string, unknown> | undefined) ?? {};
  const metricKeys = user.role === "FIELD_OFFICER" ? ["beneficiaries", "approvals"] : user.role === "SUPPORT" ? ["beneficiaries", "complaints", "operational_exceptions"] : user.role === "REVIEWER" ? ["beneficiaries", "approvals", "failed"] : ["beneficiaries", "approvals", "paid", "pending", "failed", "amounts_approved", "amounts_distributed", "budget_planned", "budget_actual", "budget_remaining"];
  const assignedComplaints = Array.isArray(summary?.assigned_complaints) ? summary.assigned_complaints as Array<Record<string, unknown>> : [];
  const roleMessage = user.is_superuser ? "Platform dashboard: select all tenants, one tenant, or one program." : user.role === "FINANCE" ? "Finance dashboard: simulated distribution, reconciliation, and budget figures for authorized programs." : user.role === "MANAGER" ? "Tenant dashboard: operational figures for your organization and selected program." : user.role === "FIELD_OFFICER" ? "Field dashboard: registration and permitted follow-up work." : user.role === "SUPPORT" ? "Support dashboard: complaints and PDM follow-up work." : user.role === "REVIEWER" ? "Reviewer dashboard: human eligibility and advisory review work." : "Read-only tenant-scoped operational dashboard.";
  return <><Panel title={user.is_superuser ? "Platform dashboard" : "Operational dashboard"}><div className="row"><div className="col-md-6">{isPlatform && <SelectField label="Tenant" name="dashboard-tenant" options={tenants.map((tenant) => ({ value: tenant.id, label: tenant.name ?? "Unnamed tenant" }))} value={tenantId} onChange={(value) => { setTenantId(value); setProgramId(""); }} />}</div><div className="col-md-6"><SelectField label="Program" name="dashboard-program" options={visiblePrograms.map((program) => ({ value: program.id, label: programLabel(program) }))} value={programId} onChange={setProgramId} /></div></div><p className="mb-0">{roleMessage}</p><p className="form-text mb-0">Scope: {String(scope.tenant_name ?? "Loading")} / {String(scope.program_name ?? "Loading")}</p></Panel><Message status="" error={error} /><section className="kpi-grid">{metricKeys.map((key) => <article className="kpi" key={key}><strong>{String(summary?.[key] ?? "—")}</strong><span>{key.replaceAll("_", " ")}</span></article>)}</section>{["ADMIN", "SUPPORT", "MANAGER"].includes(user.role) && <Panel title="Assigned complaints"><Table headings={["Beneficiary", "Program", "Category", "Severity", "Status", "Resolution"]} rows={assignedComplaints.map((complaint) => [String(complaint["beneficiary__full_name"] ?? "Beneficiary"), String(complaint["beneficiary__household__program__name"] ?? "Program"), String(complaint.category ?? ""), <Badge value={String(complaint.severity ?? "MEDIUM")} />, <Badge value={String(complaint.status ?? "OPEN")} />, String(complaint.resolution_notes ?? "Pending follow-up")])} /></Panel>}</>;
}

function TenantsPage() {
  const [tenants, setTenants] = useState<Entity[]>([]); const [status, setStatus] = useState(""); const [error, setError] = useState("");
  const load = async () => { try { setTenants(listItems(await api.list<Entity>("tenants"))); } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not load tenants."); } };
  useEffect(() => { void load(); }, []);
  const submit = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const formElement = event.currentTarget; const form = new FormData(formElement); setStatus("saving"); setError(""); try { const tenant = await api.create<Entity>("tenants", { name: form.get("name"), tenant_type: form.get("tenant_type"), default_currency: form.get("default_currency"), is_active: true }); await api.createUser({ full_name: String(form.get("manager_name")), email: String(form.get("manager_email")), password: String(form.get("manager_password")), role: "MANAGER", tenant_id: tenant.id }); formElement.reset(); await load(); setStatus("saved"); } catch (reason) { setStatus("failed"); setError(reason instanceof Error ? reason.message : "Could not create the tenant and first manager."); } };
  return <div className="row g-3"><div className="col-lg-7"><Panel title="Tenants"><Table headings={["Tenant", "Type", "Currency", "Status"]} rows={tenants.map((tenant) => [tenant.name ?? "Unnamed tenant", valueOf(tenant, "tenant_type"), valueOf(tenant, "default_currency"), <Badge value={valueOf(tenant, "is_active") === "true" ? "ACTIVE" : "INACTIVE"} />])} /></Panel></div><div className="col-lg-5"><Panel title="Create tenant and first manager"><form onSubmit={submit}><Field label="Tenant name" name="name" required /><Field label="Tenant type" name="tenant_type" required /><Field label="Default currency" name="default_currency" required /><Field label="First manager name" name="manager_name" required /><Field label="First manager email" name="manager_email" type="email" required /><Field label="Temporary password" name="manager_password" type="password" required /><button className="btn btn-primary">Create tenant</button></form><Message status={status} error={error} /></Panel></div></div>;
}

function ProgramsPage({ role }: { role: Role }) {
  const [programs, setPrograms] = useState<Entity[]>([]); const [selected, setSelected] = useState(""); const [status, setStatus] = useState(""); const [error, setError] = useState("");
  const load = async () => { try { setPrograms(listItems(await api.list<Entity>("programs"))); } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not load programs."); } };
  useEffect(() => { void load(); }, []);
  const create = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const formElement = event.currentTarget; const form = new FormData(formElement); setStatus("saving"); setError(""); try { const program = await api.create<Entity>("programs", { name: form.get("name"), country: form.get("country"), country_code: form.get("country_code"), currency: form.get("currency"), reporting_currency: form.get("reporting_currency"), currency_type: "PROGRAM", exchange_rate: "1", transfer_amount: form.get("transfer_amount"), payment_cycle: form.get("payment_cycle"), status: "DRAFT", workflow_config: { payment_enabled: form.get("payment_enabled") === "on" }, country_pack: {} }); await api.create("budgets", { program: program.id, currency: form.get("currency"), status: "DRAFT", planned_total: form.get("planned_total"), actual_total: "0", line_items: [] }); setSelected(program.id); formElement.reset(); await load(); setStatus("saved"); } catch (reason) { setStatus("failed"); setError(reason instanceof Error ? reason.message : "Could not create the program."); } };
  const saveChannel = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); if (!selected) { setError("Select a program before configuring a simulated payment channel."); return; } const formElement = event.currentTarget; const form = new FormData(formElement); setStatus("saving"); setError(""); try { await api.programChannel(selected, { channel_type: form.get("channel_type"), provider_name: form.get("provider_name"), currency: form.get("currency"), is_active: true }); formElement.reset(); setStatus("saved"); } catch (reason) { setStatus("failed"); setError(reason instanceof Error ? reason.message : "Could not save the simulated channel."); } };
  const deleteProgram = async (program: Entity) => { if (!window.confirm(`Delete ${programLabel(program)} and all associated households, beneficiaries, payments, complaints, and records? This cannot be undone.`)) return; setStatus("saving"); setError(""); try { await api.remove("programs", program.id); setSelected(""); await load(); setStatus("saved"); } catch (reason) { setStatus("failed"); setError(reason instanceof Error ? reason.message : "The program could not be deleted."); } };
  if (role === "FINANCE") return <div className="row g-3"><div className="col-xl-5"><Panel title="Configure simulated payment channel"><p className="form-text">Finance can configure only simulated channels. Program creation remains with tenant administrators and managers.</p><SelectField label="Program" name="channel-program" options={programs.map((program) => ({ value: program.id, label: programLabel(program) }))} value={selected} onChange={setSelected} required /><form onSubmit={saveChannel}><SelectField label="Channel type" name="channel_type" options={["BANK", "MOBILE_MONEY", "CASH", "VOUCHER"].map((value) => ({ value, label: value }))} required /><Field label="Simulated provider name" name="provider_name" required /><Field label="Channel currency" name="currency" required /><button className="btn btn-outline-primary">Save simulated channel</button></form><Message status={status} error={error} /></Panel></div><div className="col-xl-7"><Panel title="Authorized programs"><Table headings={["Program", "Country", "Currency", "Status"]} rows={programs.map((program) => [programLabel(program), valueOf(program, "country"), program.currency ?? "—", <Badge value={program.status ?? "DRAFT"} />])} /></Panel></div></div>;
  if (!["ADMIN", "MANAGER"].includes(role)) return <Panel title="Programs"><Table headings={["Program", "Country", "Currency", "Status"]} rows={programs.map((program) => [programLabel(program), valueOf(program, "country"), program.currency ?? "—", <Badge value={program.status ?? "DRAFT"} />])} /></Panel>;
  return <div className="row g-3"><div className="col-xl-7"><Panel title="Create program"><form onSubmit={create}><div className="row"><div className="col-md-6"><Field label="Program name" name="name" required /></div><div className="col-md-6"><Field label="Country" name="country" required /></div><div className="col-md-4"><Field label="Country code" name="country_code" required /></div><div className="col-md-4"><Field label="Program currency" name="currency" required /></div><div className="col-md-4"><Field label="Reporting currency" name="reporting_currency" required /></div><div className="col-md-6"><Field label="Transfer amount" name="transfer_amount" type="number" required /></div><div className="col-md-3"><SelectField label="Payment cycle" name="payment_cycle" options={["ONE_TIME", "MONTHLY", "QUARTERLY"].map((value) => ({ value, label: value }))} required /></div><div className="col-md-3"><Field label="Budget envelope" name="planned_total" type="number" required /></div></div><div className="form-check mb-3"><input className="form-check-input" id="payment-enabled" name="payment_enabled" type="checkbox" defaultChecked /><label className="form-check-label" htmlFor="payment-enabled">Enable simulated payments for this program</label></div><button className="btn btn-primary">Create program and budget</button></form><Message status={status} error={error} /></Panel></div><div className="col-xl-5"><Panel title="Configure simulated payment channel"><SelectField label="Program" name="channel-program" options={programs.map((program) => ({ value: program.id, label: programLabel(program) }))} value={selected} onChange={setSelected} /><form onSubmit={saveChannel}><SelectField label="Channel type" name="channel_type" options={["BANK", "MOBILE_MONEY", "CASH", "VOUCHER"].map((value) => ({ value, label: value }))} required /><Field label="Simulated provider name" name="provider_name" required /><Field label="Channel currency" name="currency" required /><button className="btn btn-outline-primary">Save simulated channel</button></form></Panel></div><div className="col-12"><Panel title="Programs"><Table headings={["Program", "Country", "Currency", "Status", "Actions"]} rows={programs.map((program) => [programLabel(program), valueOf(program, "country"), program.currency ?? "—", <Badge value={program.status ?? "DRAFT"} />, <button className="btn btn-sm btn-outline-danger" onClick={() => void deleteProgram(program)}>Delete</button>])} /></Panel></div><Message status={status} error={error} /></div>;
}

function Intake({ beneficiary, role }: { beneficiary: boolean; role: Role }) {
  const resource = beneficiary ? "beneficiaries" : "households"; const title = beneficiary ? "Register beneficiary" : "Register household";
  const [records, setRecords] = useState<Entity[]>([]); const [programs, setPrograms] = useState<Entity[]>([]); const [households, setHouseholds] = useState<Entity[]>([]); const [selectedHousehold, setSelectedHousehold] = useState(() => beneficiary ? sessionStorage.getItem("hcap_selected_household_id") ?? "" : ""); const [beneficiaryToDelete, setBeneficiaryToDelete] = useState(""); const [createdHousehold, setCreatedHousehold] = useState<Entity | null>(null); const [status, setStatus] = useState(""); const [error, setError] = useState("");
  const load = async () => { try { const [recordResponse, contextResponse] = await Promise.all([api.list<Entity>(resource), api.list<Entity>(beneficiary ? "households" : "programs")]); setRecords(listItems(recordResponse)); if (beneficiary) setHouseholds(listItems(contextResponse)); else setPrograms(listItems(contextResponse)); } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not load intake records."); } };
  useEffect(() => { void load(); }, [beneficiary]);
  useEffect(() => { if (beneficiary && selectedHousehold && households.length && !households.some((household) => household.id === selectedHousehold)) setSelectedHousehold(""); }, [beneficiary, households, selectedHousehold]);
  const save = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const formElement = event.currentTarget; const form = new FormData(formElement); setStatus("saving"); setError(""); try { if (beneficiary) { const nationalId = String(form.get("national_id") || "").trim(); const payload: Record<string, unknown> = { household: selectedHousehold, national_id: nationalId, number: nationalId, full_name: form.get("full_name"), gender: form.get("gender"), date_of_birth: form.get("date_of_birth") || null, phone_number: form.get("phone_number"), consent_given: form.get("consent_given") === "on" }; if (role !== "FIELD_OFFICER") payload.verification_status = form.get("verification_status"); await api.create<Entity>("beneficiaries", payload); } else { const household = await api.create<Entity>("households", { program: form.get("program"), household_size: Number(form.get("household_size")), location: form.get("location"), registration_date: form.get("registration_date") }); setCreatedHousehold(household); } formElement.reset(); await load(); setStatus("saved"); } catch (reason) { setStatus("failed"); setError(reason instanceof Error ? reason.message : "Correct the highlighted fields and try again."); } };
  const addBeneficiary = () => { if (!createdHousehold) return; sessionStorage.setItem("hcap_selected_household_id", createdHousehold.id); location.hash = "#/intake/beneficiaries"; };
  const canDeleteBeneficiary = beneficiary && ["ADMIN", "MANAGER"].includes(role);
  const deleteBeneficiary = async (record: Entity) => { if (!window.confirm(`Delete ${beneficiaryLabel(record)} and all associated enrollments, payment instructions, payment events, complaints, and related records? If this is the household's last beneficiary, the empty household will also be deleted. This cannot be undone.`)) return; setStatus("saving"); setError(""); try { await api.remove("beneficiaries", record.id); await load(); setStatus("saved"); } catch (reason) { setStatus("failed"); setError(reason instanceof Error ? reason.message : "The beneficiary could not be deleted."); } };
  if (beneficiary && role === "REVIEWER") return <Panel title="Beneficiaries"><Table headings={["National ID", "Household", "Full name", "Gender", "Phone number", "Program", "Verification"]} rows={records.map((record) => [valueOf(record, "national_id_reference") || "—", valueOf(record, "household_reference") || "Household", record.full_name ?? "—", valueOf(record, "gender") || "—", valueOf(record, "masked_phone") || valueOf(record, "phone_number") || "Not recorded", valueOf(record, "program_name") || "—", <Badge value={valueOf(record, "verification_status") || "PENDING"} />])} /><Message status="" error={error} /></Panel>;
  return <div className="row g-3"><div className="col-xl-5"><Panel title={title}><form onSubmit={save}>{beneficiary ? <><SelectField label="Household" name="household" options={households.map((household) => ({ value: household.id, label: householdLabel(household) }))} value={selectedHousehold} onChange={setSelectedHousehold} required /><Field label="National ID" name="national_id" required /><Field label="Full name" name="full_name" required /><SelectField label="Gender" name="gender" options={[{ value: "F", label: "Female" }, { value: "M", label: "Male" }]} /><Field label="Date of birth" name="date_of_birth" type="date" /><Field label="Phone number" name="phone_number" /><div className="form-check mb-3"><input className="form-check-input" id="consent" name="consent_given" type="checkbox" /><label className="form-check-label" htmlFor="consent">Consent given</label></div>{role !== "FIELD_OFFICER" && <SelectField label="Verification status" name="verification_status" options={["PENDING", "VERIFIED", "REJECTED"].map((value) => ({ value, label: value }))} />}</> : <><SelectField label="Program" name="program" options={programs.map((program) => ({ value: program.id, label: programLabel(program) }))} required /><Field label="Household size" name="household_size" type="number" required /><Field label="Location" name="location" required /><Field label="Registration date" name="registration_date" type="date" required /></>}<button className="btn btn-primary">Save {beneficiary ? "beneficiary" : "household"}</button></form>{createdHousehold && <div className="alert alert-success mt-3" role="status"><strong>Household saved:</strong> {householdLabel(createdHousehold)}<div className="mt-2"><button className="btn btn-success" onClick={addBeneficiary}>Add beneficiary to this household</button></div></div>}<Message status={status} error={error} /></Panel></div><div className="col-xl-7"><Panel title={beneficiary ? "Beneficiaries" : "Households"}>{beneficiary ? <Table headings={["National ID", "Household", "Full name", "Gender", "Phone number", "Program", "Verification", "Actions"]} rows={records.map((record) => [valueOf(record, "national_id_reference") || "—", valueOf(record, "household_reference") || "Household", record.full_name ?? "—", valueOf(record, "gender") || "—", valueOf(record, "masked_phone") || valueOf(record, "phone_number") || "Not recorded", valueOf(record, "program_name") || "—", <Badge value={valueOf(record, "verification_status") || "PENDING"} />, canDeleteBeneficiary ? <button className="btn btn-sm btn-outline-danger" onClick={() => void deleteBeneficiary(record)}>Delete</button> : "—"])} /> : <Table headings={["Registration reference", "Program", "Household size", "Location", "Registration date", "Status"]} rows={records.map((record) => [valueOf(record, "registration_reference") || "System reference", valueOf(record, "program_name") || "Program", valueOf(record, "household_size"), valueOf(record, "location"), valueOf(record, "registration_date"), <Badge value="REGISTERED" />])} />}</Panel></div></div>;
}

function EligibilityPage() {
  const [programs, setPrograms] = useState<Entity[]>([]); const [beneficiaries, setBeneficiaries] = useState<Entity[]>([]); const [enrollments, setEnrollments] = useState<Entity[]>([]); const [signals, setSignals] = useState<Entity[]>([]); const [tasks, setTasks] = useState<Entity[]>([]); const [status, setStatus] = useState(""); const [error, setError] = useState("");
  const load = async () => { try { const [programResponse, beneficiaryResponse, enrollmentResponse, signalResponse, taskResponse] = await Promise.all([api.list<Entity>("programs"), api.list<Entity>("beneficiaries"), api.list<Entity>("enrollments"), api.list<Entity>("ai-signals"), api.list<Entity>("review-tasks")]); setPrograms(listItems(programResponse)); setBeneficiaries(listItems(beneficiaryResponse)); setEnrollments(listItems(enrollmentResponse)); setSignals(listItems(signalResponse)); setTasks(listItems(taskResponse)); } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not load eligibility work."); } };
  useEffect(() => { void load(); }, []);
  const submit = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const formElement = event.currentTarget; const form = new FormData(formElement); setStatus("saving"); setError(""); try { await api.create("enrollments", { program: form.get("program"), beneficiary: form.get("beneficiary"), eligibility_status: form.get("eligibility_status"), status: "ENROLLED" }); formElement.reset(); await load(); setStatus("saved"); } catch (reason) { setStatus("failed"); setError(reason instanceof Error ? reason.message : "Could not create the eligibility review."); } };
  const updateEligibility = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const formElement = event.currentTarget; const form = new FormData(formElement); setStatus("saving"); setError(""); try { await api.update("enrollments", String(form.get("eligibility_enrollment")), { eligibility_status: form.get("updated_eligibility_status") }); await load(); setStatus("saved"); } catch (reason) { setStatus("failed"); setError(reason instanceof Error ? reason.message : "Could not update the eligibility status."); } };
  return <div className="row g-3"><div className="col-lg-5"><Panel title="Eligibility review"><form onSubmit={submit}><SelectField label="Program" name="program" options={programs.map((program) => ({ value: program.id, label: programLabel(program) }))} required /><SelectField label="Beneficiary" name="beneficiary" options={beneficiaries.map((item) => ({ value: item.id, label: `${beneficiaryLabel(item)} — ${valueOf(item, "household_reference")}` }))} required /><SelectField label="Eligibility status" name="eligibility_status" options={["PENDING", "ELIGIBLE", "INELIGIBLE", "ON_HOLD"].map((value) => ({ value, label: value }))} required /><button className="btn btn-primary">Create human review</button></form><Message status={status} error={error} /></Panel><Panel title="Update eligibility status"><p className="section-lead">Select an existing enrollment to save a new eligibility status.</p><form onSubmit={updateEligibility}><SelectField label="Enrollment" name="eligibility_enrollment" options={enrollments.map((item) => ({ value: item.id, label: enrollmentLabel(item) }))} required /><SelectField label="New eligibility status" name="updated_eligibility_status" options={["PENDING", "ELIGIBLE", "INELIGIBLE", "ON_HOLD"].map((value) => ({ value, label: value }))} required /><button className="btn btn-primary">Save eligibility status</button></form><Message status={status} error={error} /></Panel></div><div className="col-lg-7"><Panel title="Eligibility and advisory work"><Table headings={["Beneficiary", "Program", "Eligibility", "Enrollment status"]} rows={enrollments.map((item) => [enrollmentLabel(item), valueOf(item, "program_name"), <Badge value={valueOf(item, "eligibility_status") || "PENDING"} />, <Badge value={item.status ?? "ENROLLED"} />])} /><Table headings={["Open advisory signals", "Human review tasks", "Status"]} rows={[...signals.slice(0, 5).map((signal) => [valueOf(signal, "signal_type"), valueOf(signal, "reason"), <Badge value={signal.status ?? "OPEN"} />]), ...tasks.slice(0, 5).map((task) => [valueOf(task, "task_type"), valueOf(task, "resolution"), <Badge value={task.status ?? "OPEN"} />])]} /></Panel></div></div>;
}

function EnrollmentDecisionPage() {
  const [enrollments, setEnrollments] = useState<Entity[]>([]); const [status, setStatus] = useState(""); const [error, setError] = useState("");
  const load = async () => { try { setEnrollments(listItems(await api.list<Entity>("enrollments"))); } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not load enrollment decisions."); } };
  useEffect(() => { void load(); }, []);
  const submit = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const formElement = event.currentTarget; const form = new FormData(formElement); setStatus("saving"); setError(""); try { await api.update("enrollments", String(form.get("enrollment")), { status: form.get("status") }); formElement.reset(); await load(); setStatus("saved"); } catch (reason) { setStatus("failed"); setError(reason instanceof Error ? reason.message : "Could not record the enrollment decision."); } };
  return <div className="row g-3"><div className="col-lg-5"><Panel title="Enrollment decision"><form onSubmit={submit}><SelectField label="Enrollment" name="enrollment" options={enrollments.map((item) => ({ value: item.id, label: enrollmentLabel(item) }))} required /><SelectField label="Decision" name="status" options={["APPROVED", "REJECTED"].map((value) => ({ value, label: value }))} required /><button className="btn btn-primary">Record human decision</button></form><Message status={status} error={error} /></Panel></div><div className="col-lg-7"><Panel title="Enrollment history"><Table headings={["Beneficiary", "Program", "Eligibility", "Decision"]} rows={enrollments.map((item) => [enrollmentLabel(item), valueOf(item, "program_name"), <Badge value={valueOf(item, "eligibility_status") || "PENDING"} />, <Badge value={item.status ?? "ENROLLED"} />])} /></Panel></div></div>;
}

function PaymentsPage({ role }: { role: Role }) {
  const [programs, setPrograms] = useState<Entity[]>([]); const [enrollments, setEnrollments] = useState<Entity[]>([]); const [batches, setBatches] = useState<Entity[]>([]); const [instructions, setInstructions] = useState<Entity[]>([]); const [channels, setChannels] = useState<Entity[]>([]); const [programId, setProgramId] = useState(""); const [events, setEvents] = useState<Entity[]>([]); const [status, setStatus] = useState(""); const [error, setError] = useState("");
  const canWrite = role !== "AUDITOR";
  const load = async () => { try { const [programResponse, enrollmentResponse, batchResponse, instructionResponse] = await Promise.all([api.list<Entity>("programs"), api.list<Entity>("enrollments"), api.list<Entity>("payment-batches"), api.list<Entity>("payment-instructions")]); setPrograms(listItems(programResponse)); setEnrollments(listItems(enrollmentResponse)); setBatches(listItems(batchResponse)); setInstructions(listItems(instructionResponse)); } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not load simulated payment records."); } };
  useEffect(() => { void load(); }, []);
  useEffect(() => { const loadChannels = async () => { if (!programId) { setChannels([]); return; } try { setChannels(await api.get<Entity[]>(`/programs/${programId}/channels/`)); } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not load simulated payment channels."); } }; void loadChannels(); }, [programId]);
  const createBatch = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const formElement = event.currentTarget; const form = new FormData(formElement); setStatus("saving"); setError(""); try { await api.create("payment-batches", { program: form.get("program"), name: form.get("name"), status: "DRAFT", provider_response: {} }); formElement.reset(); await load(); setStatus("saved"); } catch (reason) { setStatus("failed"); setError(reason instanceof Error ? reason.message : "Could not create the simulated payment batch."); } };
  const createInstruction = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const formElement = event.currentTarget; const form = new FormData(formElement); const enrollment = enrollments.find((item) => item.id === String(form.get("enrollment"))); if (!enrollment) { setError("Select an approved enrollment."); return; } setStatus("saving"); setError(""); try { const payload: Record<string, unknown> = { enrollment: enrollment.id, beneficiary: valueOf(enrollment, "beneficiary"), channel_config: form.get("channel_config"), amount: form.get("amount"), currency: form.get("currency") }; if (form.get("batch")) payload.batch = form.get("batch"); await api.create("payment-instructions", payload); formElement.reset(); await load(); setStatus("saved"); } catch (reason) { setStatus("failed"); setError(reason instanceof Error ? reason.message : "Could not create the simulated payment instruction."); } };
  const simulate = async (instructionId: string, outcome: "submit" | "success" | "failure" | "retry" | "reversal") => { setStatus("saving"); setError(""); try { await api.simulatePayment(instructionId, outcome); await load(); setStatus("saved"); } catch (reason) { setStatus("failed"); setError(reason instanceof Error ? reason.message : "Could not simulate this outcome."); } };
  const chooseStatus = (instruction: Entity, nextStatus: string) => { const outcome = nextStatus === "SUBMITTED" ? "submit" : nextStatus === "SUCCESS" ? "success" : nextStatus === "FAILED" ? "failure" : null; if (outcome && nextStatus !== instruction.status) void simulate(instruction.id, outcome); };
  const paymentStatusOptions = (instruction: Entity) => { const current = String(instruction.status ?? "CREATED"); return current === "CREATED" ? ["CREATED", "SUBMITTED"] : current === "SUBMITTED" ? ["SUBMITTED", "SUCCESS", "FAILED"] : [current]; };
  const viewEvents = async (instructionId: string) => { try { setEvents(await api.get<Entity[]>(`/payment-instructions/${instructionId}/events/`)); } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not load payment events."); } };
  const selectedEnrollments = enrollments.filter((item) => item.status === "APPROVED" && (!programId || valueOf(item, "program") === programId));
  const selectedBatches = batches.filter((batch) => !programId || valueOf(batch, "program") === programId);
  return <div className="row g-3"><div className="col-xl-5">{canWrite && <><Panel title="Create simulated payment batch"><form onSubmit={createBatch}><SelectField label="Program" name="program" options={programs.map((program) => ({ value: program.id, label: programLabel(program) }))} required /><Field label="Batch name" name="name" required /><button className="btn btn-outline-primary">Create simulated batch</button></form></Panel><Panel title="Create simulated payment instruction"><SelectField label="Program" name="payment-program" options={programs.map((program) => ({ value: program.id, label: programLabel(program) }))} value={programId} onChange={setProgramId} /><form onSubmit={createInstruction}><SelectField label="Approved enrollment" name="enrollment" options={selectedEnrollments.map((item) => ({ value: item.id, label: enrollmentLabel(item) }))} required /><SelectField label="Simulated payment channel" name="channel_config" options={channels.map((channel) => ({ value: channel.id, label: `${valueOf(channel, "provider_name")} (${valueOf(channel, "channel_type")})` }))} required /><SelectField label="Payment batch" name="batch" options={selectedBatches.map((batch) => ({ value: batch.id, label: batch.name ?? "Simulated batch" }))} /><Field label="Amount" name="amount" type="number" required /><Field label="Currency" name="currency" required /><p className="form-text">This uses the built-in simulator only. No external payment provider is called.</p><button className="btn btn-primary">Create simulated instruction</button></form></Panel></>}</div><div className="col-xl-7"><Panel title="Simulated payment instructions"><Table headings={["Beneficiary", "Program", "Channel", "Amount", "Status", "Actions"]} rows={instructions.map((item) => [valueOf(item, "beneficiary_name") || "Beneficiary", valueOf(item, "program_name") || "Program", valueOf(item, "channel_name") || "Simulated channel", `${valueOf(item, "amount")} ${valueOf(item, "currency")}`, canWrite ? <select className="form-select form-select-sm" aria-label={`Status for ${valueOf(item, "beneficiary_name") || "beneficiary"}`} value={String(item.status ?? "CREATED")} onChange={(event) => chooseStatus(item, event.target.value)}>{paymentStatusOptions(item).map((option) => <option key={option} value={option}>{option}</option>)}</select> : <Badge value={item.status ?? "CREATED"} />, <button className="btn btn-sm btn-outline-secondary" onClick={() => void viewEvents(item.id)}>Events</button>])} /></Panel>{events.length > 0 && <Panel title="Append-only payment events"><Table headings={["Event", "Provider status", "From", "To", "Time"]} rows={events.map((event) => [valueOf(event, "event_type"), valueOf(event, "provider_status"), valueOf(event, "from_status") || "—", valueOf(event, "to_status"), valueOf(event, "created_at")])} /></Panel>}<Message status={status} error={error} /></div></div>;
}

function ComplaintsPage({ role }: { role: Role }) {
  const [beneficiaries, setBeneficiaries] = useState<Entity[]>([]);
  const [complaints, setComplaints] = useState<Entity[]>([]);
  const [users, setUsers] = useState<PlatformUser[]>([]);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const canAssign = role === "ADMIN" || role === "MANAGER";

  const load = async () => {
    try {
      const [beneficiaryResponse, complaintResponse] = await Promise.all([
        api.list<Entity>("beneficiaries"),
        api.list<Entity>("complaints"),
      ]);
      setBeneficiaries(listItems(beneficiaryResponse));
      setComplaints(listItems(complaintResponse));
      if (canAssign) setUsers(listItems(await api.users()));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not load complaint records.");
    }
  };

  useEffect(() => { void load(); }, [canAssign]);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setStatus("saving");
    setError("");
    try {
      await api.create("complaints", {
        beneficiary: form.get("beneficiary"),
        category: form.get("category"),
        description: form.get("description"),
        severity: form.get("severity"),
        status: "OPEN",
      });
      formElement.reset();
      await load();
      setStatus("saved");
    } catch (reason) {
      setStatus("failed");
      setError(reason instanceof Error ? reason.message : "Could not register the complaint.");
    }
  };

  const updateComplaint = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const complaintId = String(form.get("complaint"));
    setStatus("saving");
    setError("");
    try {
      const payload: Record<string, unknown> = {
        status: form.get("complaint_status"),
        resolution_notes: form.get("resolution_notes"),
      };
      if (canAssign && form.get("assigned_to")) payload.assigned_to = form.get("assigned_to");
      await api.update("complaints", complaintId, payload);
      formElement.reset();
      await load();
      setStatus("saved");
    } catch (reason) {
      setStatus("failed");
      setError(reason instanceof Error ? reason.message : "Could not update the complaint.");
    }
  };

  const openComplaints = complaints.filter((complaint) => !["RESOLVED", "CLOSED"].includes(String(complaint.status)));
  return <div className="row g-3">
    <div className="col-lg-5"><Panel title="Register complaint"><form onSubmit={submit}><SelectField label="Beneficiary / household" name="beneficiary" options={beneficiaries.map((item) => ({ value: item.id, label: `${beneficiaryLabel(item)} — ${valueOf(item, "program_name") || "Program"}` }))} required /><Field label="Category" name="category" required /><Textarea label="Description" name="description" required /><SelectField label="Severity" name="severity" options={["LOW", "MEDIUM", "HIGH", "CRITICAL"].map((value) => ({ value, label: value }))} required /><button className="btn btn-primary">Register complaint</button></form></Panel><Panel title="Assign, investigate, or resolve"><form onSubmit={updateComplaint}><SelectField label="Open complaint" name="complaint" options={openComplaints.map((item) => ({ value: item.id, label: `${valueOf(item, "category")} — ${valueOf(item, "beneficiary_name") || "Beneficiary"}` }))} required />{canAssign && <SelectField label="Assign to tenant user" name="assigned_to" options={users.map((person) => ({ value: person.id, label: `${person.full_name} (${person.role})` }))} />}<SelectField label="Case status" name="complaint_status" options={["IN_PROGRESS", "RESOLVED", "CLOSED"].map((value) => ({ value, label: value }))} required /><Textarea label="Investigation or resolution note" name="resolution_notes" required /><button className="btn btn-outline-primary" disabled={!openComplaints.length}>Save complaint update</button></form><Message status={status} error={error} /></Panel></div>
    <div className="col-lg-7"><Panel title="Complaints"><Table headings={role === "SUPPORT" ? ["Beneficiary", "Program", "Household", "Category", "Severity", "Status", "Resolution"] : ["Beneficiary", "Program", "Household", "Category", "Severity", "Assigned to", "Status", "Resolution"]} rows={complaints.map((item) => { const row = [valueOf(item, "beneficiary_name") || "Beneficiary", valueOf(item, "program_name") || "Program", valueOf(item, "household_reference") || "Household", valueOf(item, "category"), <Badge value={valueOf(item, "severity") || "MEDIUM"} />]; return role === "SUPPORT" ? [...row, <Badge value={item.status ?? "OPEN"} />, valueOf(item, "resolution_notes") || "—"] : [...row, valueOf(item, "assigned_to_name") || "Unassigned", <Badge value={item.status ?? "OPEN"} />, valueOf(item, "resolution_notes") || "—"]; })} /></Panel></div>
  </div>;
}

function BudgetsPage({ role }: { role: Role }) {
  const [programs, setPrograms] = useState<Entity[]>([]); const [budgets, setBudgets] = useState<Entity[]>([]); const [status, setStatus] = useState(""); const [error, setError] = useState("");
  const canWrite = role !== "AUDITOR";
  const load = async () => { try { const [programResponse, budgetResponse] = await Promise.all([api.list<Entity>("programs"), api.list<Entity>("budgets")]); setPrograms(listItems(programResponse)); setBudgets(listItems(budgetResponse)); } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not load budgets."); } };
  useEffect(() => { void load(); }, []);
  const submit = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const formElement = event.currentTarget; const form = new FormData(formElement); const programId = String(form.get("program")); const existing = budgets.find((budget) => valueOf(budget, "program") === programId); const payload = { program: programId, currency: form.get("currency"), planned_total: form.get("planned_total"), actual_total: form.get("actual_total"), status: form.get("status"), line_items: [] }; setStatus("saving"); setError(""); try { if (existing) await api.update("budgets", existing.id, payload); else await api.create("budgets", payload); formElement.reset(); await load(); setStatus("saved"); } catch (reason) { setStatus("failed"); setError(reason instanceof Error ? reason.message : "Could not save the budget."); } };
  return <div className="row g-3">{canWrite && <div className="col-lg-5"><Panel title="Manage program budget"><form onSubmit={submit}><SelectField label="Program" name="program" options={programs.map((program) => ({ value: program.id, label: programLabel(program) }))} required /><Field label="Currency" name="currency" required /><Field label="Budget planned total" name="planned_total" type="number" required /><Field label="Budget actual total" name="actual_total" type="number" required /><SelectField label="Budget status" name="status" options={["DRAFT", "ACTIVE", "CLOSED"].map((value) => ({ value, label: value }))} required /><button className="btn btn-primary">Save budget</button></form><Message status={status} error={error} /></Panel></div>}<div className={canWrite ? "col-lg-7" : "col-12"}><Panel title="Budget summaries"><Table headings={["Program", "Planned", "Actual", "Remaining", "Status"]} rows={budgets.map((budget) => { const planned = Number(valueOf(budget, "planned_total") || 0); const actual = Number(valueOf(budget, "actual_total") || 0); return [valueOf(budget, "program_name") || "Program", `${planned} ${valueOf(budget, "currency")}`, `${actual} ${valueOf(budget, "currency")}`, `${planned - actual} ${valueOf(budget, "currency")}`, <Badge value={budget.status ?? "DRAFT"} />]; })} /></Panel></div></div>;
}

function PdmPage({ user }: { user: PlatformUser }) {
  const [tenants, setTenants] = useState<Entity[]>([]); const [programs, setPrograms] = useState<Entity[]>([]); const [responses, setResponses] = useState<Entity[]>([]); const [tenantId, setTenantId] = useState(""); const [programId, setProgramId] = useState(""); const [summary, setSummary] = useState<Record<string, unknown> | null>(null); const [status, setStatus] = useState(""); const [error, setError] = useState("");
  const canEnter = ["ADMIN", "FIELD_OFFICER", "MANAGER"].includes(user.role);
  const queryForSelection = () => { const query = new URLSearchParams(); if (user.is_superuser && tenantId) query.set("tenant", tenantId); if (programId) query.set("program", programId); return query; };
  const load = async () => { try { const query = queryForSelection(); const [programResponse, tenantResponse, responseRecords] = await Promise.all([api.list<Entity>("programs"), user.is_superuser ? api.list<Entity>("tenants") : Promise.resolve([] as Entity[]), api.get<Entity[]>(`/pdm/${query.toString() ? `?${query}` : ""}`)]); setPrograms(listItems(programResponse)); setTenants(user.is_superuser ? listItems(tenantResponse as Entity[] | { results: Entity[] }) : []); setResponses(responseRecords); } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not load PDM records."); } };
  const loadSummary = async () => { const query = queryForSelection(); try { setError(""); setSummary(await api.get(`/pdm/summary/${query.toString() ? `?${query}` : ""}`)); } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not load PDM summary."); } };
  useEffect(() => { void load(); }, [user.is_superuser, tenantId, programId]);
  useEffect(() => { if (programId) void loadSummary(); else setSummary(null); }, [user.is_superuser, tenantId, programId]);
  const visiblePrograms = useMemo(() => user.is_superuser ? (tenantId ? programs.filter((program) => valueOf(program, "tenant") === tenantId) : []) : programs, [programs, tenantId, user.is_superuser]);
  const submit = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const formElement = event.currentTarget; const form = new FormData(formElement); setStatus("saving"); setError(""); try { await api.create("pdm", { program: form.get("program"), received_count: form.get("received_count"), access_problem_rate: form.get("access_problem_rate") || 0, satisfaction: form.get("satisfaction") || 0 }); formElement.reset(); await load(); await loadSummary(); setStatus("saved"); } catch (reason) { setStatus("failed"); setError(reason instanceof Error ? reason.message : "Could not submit the PDM response."); } };
  const summaryChannels = Array.isArray(summary?.channels) && summary.channels.length ? (summary.channels as string[]).join(", ") : "Not available";
  const summaryLocations = Array.isArray(summary?.locations) && summary.locations.length ? (summary.locations as string[]).join(", ") : "Not available";
  const number = (key: string) => String(summary?.[key] ?? 0);
  return <div className="row g-3">{canEnter && <div className="col-lg-5"><Panel title="Post-Distribution Monitoring"><p className="form-text">Record and review whether assistance reached people as intended after distribution.</p><form onSubmit={submit}><SelectField label="Program" name="program" options={visiblePrograms.map((program) => ({ value: program.id, label: programLabel(program) }))} value={programId} onChange={setProgramId} required /><div className="alert alert-light border" role="status"><div><strong>Beneficiaries paid:</strong> {number("paid_beneficiaries")}</div><div><strong>Already recorded:</strong> {number("recorded_recipients")}</div><div><strong>Available to record:</strong> {number("remaining_recipients")}</div></div><Field label="People who received assistance" name="received_count" type="number" required help="Enter no more than the available recipients." /><div className="mb-3"><label className="form-label">Received rate</label><div className="form-control-plaintext">{number("received_rate")}%</div></div><div className="mb-3"><label className="form-label">Amount received</label><div className="form-control-plaintext">{number("amount_received")}</div></div><Field label="Access problem rate" name="access_problem_rate" type="number" /><div className="mb-3"><label className="form-label">Complaint rate</label><div className="form-control-plaintext">{number("complaint_rate")}%</div></div><Field label="Satisfaction" name="satisfaction" type="number" /><button className="btn btn-primary" disabled={!programId || Number(summary?.remaining_recipients ?? 0) < 1}>Submit PDM response</button></form><Message status={status} error={error} /></Panel></div>}<div className={canEnter ? "col-lg-7" : "col-12"}><Panel title="PDM summary"><div className="row"><div className="col-md-6">{user.is_superuser && <SelectField label="Tenant" name="pdm-tenant" options={tenants.map((tenant) => ({ value: tenant.id, label: tenant.name ?? "Unnamed tenant" }))} value={tenantId} onChange={(value) => { setTenantId(value); setProgramId(""); }} />}</div><div className="col-md-6"><SelectField label="Program" name="pdm-program" options={visiblePrograms.map((program) => ({ value: program.id, label: programLabel(program) }))} value={programId} onChange={setProgramId} /></div></div><button className="btn btn-outline-primary mb-3" onClick={() => void loadSummary()}>View calculated summary</button>{summary && <div className="row g-2"><div className="col-md-4"><div className="border rounded p-3 h-100"><small>Recipients recorded</small><div className="fs-4 fw-semibold">{number("recorded_recipients")} / {number("paid_beneficiaries")}</div><small>{number("received_rate")}% received</small></div></div><div className="col-md-4"><div className="border rounded p-3 h-100"><small>Amount received</small><div className="fs-4 fw-semibold">{number("amount_received")}</div><small>Distributed: {number("distributed_amount")}</small></div></div><div className="col-md-4"><div className="border rounded p-3 h-100"><small>Complaints</small><div className="fs-4 fw-semibold">{number("complaints")}</div><small>{number("complaint_rate")}% complaint rate</small></div></div><div className="col-md-6"><div className="border rounded p-3 h-100"><strong>Channel</strong><div>{summaryChannels}</div></div></div><div className="col-md-6"><div className="border rounded p-3 h-100"><strong>Operational location</strong><div>{summaryLocations}</div></div></div></div>}</Panel><Panel title="PDM responses"><Table headings={["Program", "Recipients", "Channel", "Operational location", "Received rate", "Amount received", "Satisfaction"]} rows={responses.map((response) => [valueOf(response, "program__name") || "Program", valueOf(response, "received_count"), valueOf(response, "channel") || "Not available", valueOf(response, "location") || "Not available", `${valueOf(response, "received_rate")}%`, valueOf(response, "amount_received"), valueOf(response, "satisfaction")])} /></Panel><Message status="" error={error} /></div></div>;
}

function ImportsPage() {
  const [programs, setPrograms] = useState<Entity[]>([]); const [programId, setProgramId] = useState(""); const [file, setFile] = useState<File | null>(null); const [confirmationToken, setConfirmationToken] = useState(""); const [result, setResult] = useState<Record<string, unknown> | null>(null); const [status, setStatus] = useState(""); const [error, setError] = useState("");
  useEffect(() => { api.list<Entity>("programs").then((response) => setPrograms(listItems(response))).catch((reason) => setError(reason instanceof Error ? reason.message : "Could not load programs.")); }, []);
  const preview = async () => { if (!file || !programId) { setError("Select a program and a CSV or XLSX file."); return; } setStatus("saving"); setError(""); try { const response = await api.importFile<Record<string, unknown>>(file, programId); setResult(response); setConfirmationToken(String(response.confirmation_token ?? "")); setStatus("saved"); } catch (reason) { setStatus("failed"); setError(reason instanceof Error ? reason.message : "Could not preview the import."); } };
  const confirm = async () => { if (!file || !programId || !confirmationToken) return; setStatus("saving"); setError(""); try { setResult(await api.importFile<Record<string, unknown>>(file, programId, confirmationToken)); setConfirmationToken(""); setStatus("saved"); } catch (reason) { setStatus("failed"); setError(reason instanceof Error ? reason.message : "Could not save valid rows."); } };
  return <Panel title="Controlled CSV/XLSX import"><p>Required columns: client_generated_id, household_size, location, national_id (or legacy beneficiary_number), full_name. The import file has its own client reference column; online household registration does not require it.</p><SelectField label="Program" name="import-program" options={programs.map((program) => ({ value: program.id, label: programLabel(program) }))} value={programId} onChange={(value) => { setProgramId(value); setConfirmationToken(""); }} required /><div className="mb-3"><label className="form-label" htmlFor="import-file">CSV or XLSX file *</label><input className="form-control" id="import-file" type="file" accept=".csv,.xlsx" onChange={(event) => { setFile(event.target.files?.[0] ?? null); setConfirmationToken(""); }} /></div><button className="btn btn-primary me-2" onClick={() => void preview()}>Preview and validate</button>{confirmationToken && <button className="btn btn-success" onClick={() => void confirm()}>Confirm and save valid rows</button>}{result && <Table headings={["Result", "Value"]} rows={Object.entries(result).filter(([key]) => !["errors", "preview_rows", "confirmation_token"].includes(key)).map(([key, value]) => [key.replaceAll("_", " "), typeof value === "object" ? JSON.stringify(value) : String(value)])} />}{Array.isArray(result?.errors) && <Table headings={["Row", "Field", "Message"]} rows={(result.errors as Array<{ row: number; field: string; message: string }>).map((item) => [String(item.row), item.field, item.message])} />}<Message status={status} error={error} /></Panel>;
}

function ActivitiesPage({ role }: { role: Role }) {
  const [programs, setPrograms] = useState<Entity[]>([]); const [activities, setActivities] = useState<Entity[]>([]); const [dependencies, setDependencies] = useState<Entity[]>([]); const [users, setUsers] = useState<PlatformUser[]>([]); const [status, setStatus] = useState(""); const [error, setError] = useState("");
  const canWrite = role === "ADMIN" || role === "MANAGER";
  const canReadDependencies = ["ADMIN", "MANAGER", "AUDITOR"].includes(role);
  const activityStatus = (value: unknown) => role === "REVIEWER" ? ({ NOT_STARTED: "Start", IN_PROGRESS: "In progress", COMPLETED: "Finish", BLOCKED: "Blocked" }[String(value)] ?? String(value)) : String(value);
  const load = async () => { try { const [programResponse, activityResponse] = await Promise.all([api.list<Entity>("programs"), api.list<Entity>("program-activities")]); setPrograms(listItems(programResponse)); setActivities(listItems(activityResponse)); if (canReadDependencies) setDependencies(listItems(await api.list<Entity>("activity-dependencies"))); else setDependencies([]); if (canWrite) setUsers(listItems(await api.users())); } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not load program activities."); } };
  useEffect(() => { void load(); }, [canReadDependencies, canWrite]);
  const createActivity = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const formElement = event.currentTarget; const form = new FormData(formElement); setStatus("saving"); setError(""); try { await api.create("program-activities", { program: form.get("program"), name: form.get("name"), workstream: form.get("workstream"), owner: form.get("owner") || null, status: form.get("activity_status"), progress: form.get("progress") || 0, risk: form.get("risk") || "" }); formElement.reset(); await load(); setStatus("saved"); } catch (reason) { setStatus("failed"); setError(reason instanceof Error ? reason.message : "Could not save the activity."); } };
  const createDependency = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const formElement = event.currentTarget; const form = new FormData(formElement); setStatus("saving"); setError(""); try { await api.create("activity-dependencies", { predecessor: form.get("predecessor"), successor: form.get("successor"), relationship_type: "FS", lag_days: Number(form.get("lag_days") || 0), reason: form.get("reason") || "" }); formElement.reset(); await load(); setStatus("saved"); } catch (reason) { setStatus("failed"); setError(reason instanceof Error ? reason.message : "Could not save the dependency. A dependency cannot cross tenants, programs, or create a cycle."); } };
  const activityOptions = activities.map((activity) => ({ value: activity.id, label: `${valueOf(activity, "name")} — ${valueOf(activity, "program_name") || "Program"}` }));
  return <div className="row g-3">{canWrite && <div className="col-lg-5"><Panel title="Add program activity"><form onSubmit={createActivity}><SelectField label="Program" name="program" options={programs.map((program) => ({ value: program.id, label: programLabel(program) }))} required /><Field label="Activity name" name="name" required /><Field label="Workstream" name="workstream" /><SelectField label="Owner" name="owner" options={users.map((user) => ({ value: user.id, label: `${user.full_name} (${user.role})` }))} /><SelectField label="Status" name="activity_status" options={["NOT_STARTED", "IN_PROGRESS", "BLOCKED", "COMPLETED"].map((value) => ({ value, label: value }))} required /><Field label="Progress (%)" name="progress" type="number" /><Field label="Risk" name="risk" /><button className="btn btn-primary">Save activity</button></form></Panel><Panel title="Add finish-to-start dependency"><form onSubmit={createDependency}><SelectField label="Predecessor activity" name="predecessor" options={activityOptions} required /><SelectField label="Successor activity" name="successor" options={activityOptions} required /><Field label="Lag days" name="lag_days" type="number" /><Field label="Reason" name="reason" /><button className="btn btn-outline-primary">Save dependency</button></form></Panel><Message status={status} error={error} /></div>}<div className={canWrite ? "col-lg-7" : "col-12"}><Panel title="Precedence diagram activity list"><Table headings={["Activity", "Program", "Owner", "Status", "Progress", "Risk"]} rows={activities.map((activity) => [valueOf(activity, "name"), valueOf(activity, "program_name") || "Program", valueOf(activity, "owner_name") || "Unassigned", <Badge value={activityStatus(activity.status ?? "NOT_STARTED")} />, `${valueOf(activity, "progress") || 0}%`, valueOf(activity, "risk") || "—"])} /></Panel>{canReadDependencies && <Panel title="Dependencies"><Table headings={["Predecessor", "Successor", "Relationship", "Lag"]} rows={dependencies.map((dependency) => [valueOf(dependency, "predecessor_name") || "Activity", valueOf(dependency, "successor_name") || "Activity", valueOf(dependency, "relationship_type") || "FS", valueOf(dependency, "lag_days")])} /></Panel>}</div></div>;
}

function AutomationPage({ role }: { role: Role }) {
  const [data, setData] = useState<{ status: string; message: string; rules: Entity[]; signals_count: number; review_tasks_open: number; advisory_only: boolean } | null>(null);
  const [beneficiaries, setBeneficiaries] = useState<Entity[]>([]);
  const [batches, setBatches] = useState<Entity[]>([]);
  const [programs, setPrograms] = useState<Entity[]>([]);
  const [signals, setSignals] = useState<Entity[]>([]);
  const [reviewTasks, setReviewTasks] = useState<Entity[]>([]);
  const [reconciliationItems, setReconciliationItems] = useState<Entity[]>([]);
  const [pipeline, setPipeline] = useState<Record<string, unknown> | null>(null);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [selectedAutomationAction, setSelectedAutomationAction] = useState("");
  const canDeduplicate = ["ADMIN", "MANAGER", "REVIEWER"].includes(role);
  const canReconcile = ["ADMIN", "MANAGER", "FINANCE", "AUDITOR"].includes(role);
  const canUseCopilot = ["ADMIN", "MANAGER", "REVIEWER", "AUDITOR"].includes(role);
  const canExecuteRules = ["ADMIN", "MANAGER"].includes(role);
  const canInspectPipeline = ["ADMIN", "MANAGER", "AUDITOR"].includes(role);

  const load = async () => {
    try {
      const statusResponse = await api.get<typeof data>("/ai/status/");
      setData(statusResponse);
      const programResponse = await api.list<Entity>("programs");
      setPrograms(listItems(programResponse));
      if (canDeduplicate) {
        const [beneficiaryResponse, signalResponse, taskResponse] = await Promise.all([
          api.list<Entity>("beneficiaries"),
          api.list<Entity>("ai-signals"),
          api.list<Entity>("review-tasks"),
        ]);
        setBeneficiaries(listItems(beneficiaryResponse));
        setSignals(listItems(signalResponse));
        setReviewTasks(listItems(taskResponse));
      } else {
        setBeneficiaries([]);
        setSignals([]);
        setReviewTasks([]);
      }
      if (canReconcile) {
        const [batchResponse, reconciliationResponse] = await Promise.all([
          api.list<Entity>("payment-batches"),
          api.list<Entity>("reconciliation-items"),
        ]);
        setBatches(listItems(batchResponse));
        setReconciliationItems(listItems(reconciliationResponse));
      } else {
        setBatches([]);
        setReconciliationItems([]);
      }
      if (canInspectPipeline) setPipeline(await api.get<Record<string, unknown>>("/pipeline/status/"));
      else setPipeline(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not load AI and automation status.");
    }
  };

  useEffect(() => { void load(); }, [role]);

  const run = async (event: FormEvent<HTMLFormElement>, path: string, payload: (form: FormData) => Record<string, unknown>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setStatus("saving");
    setError("");
    try {
      setResult(await api.post<Record<string, unknown>>(path, payload(form)));
      await load();
      setStatus("saved");
    } catch (reason) {
      setStatus("failed");
      setError(reason instanceof Error ? reason.message : "Could not run the controlled service.");
    }
  };

  const createRule = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setStatus("saving");
    setError("");
    try {
      await api.create("automation-rules", {
        program: form.get("rule-program") || null,
        event_name: form.get("event_name"),
        action_name: form.get("action_name"),
        conditions: {},
        action_config: {},
        is_active: form.get("rule_active") === "on",
        priority: Number(form.get("rule_priority") || 100),
      });
      formElement.reset();
      await load();
      setStatus("saved");
    } catch (reason) {
      setStatus("failed");
      setError(reason instanceof Error ? reason.message : "Could not save the automation rule.");
    }
  };

  const activeRules = (data?.rules ?? []).filter((rule) => valueOf(rule, "is_active") === "true");
  const openSignals = signals.filter((signal) => signal.status === "OPEN");
  const openReviewTasks = reviewTasks.filter((task) => ["OPEN", "ASSIGNED"].includes(String(task.status)));
  const unresolvedReconciliationItems = reconciliationItems.filter((item) => ["OPEN", "IN_REVIEW"].includes(String(item.status)));
  const canResolveReconciliation = ["ADMIN", "MANAGER", "FINANCE"].includes(role);
  const automationResultRows = result && result.outcome ? (() => {
    const plannedAction = (result.planned_action && typeof result.planned_action === "object") ? result.planned_action as Record<string, unknown> : {};
    const eventPayload = (result.event_payload && typeof result.event_payload === "object") ? result.event_payload as Record<string, unknown> : {};
    const rows: [string, string][] = [["Outcome", result.outcome === "HUMAN_REVIEW_TASK_CREATED" ? "Human review task created" : result.outcome === "DRY_RUN_NOT_EXECUTED" ? "Dry run — no automatic action executed" : readableCode(result.outcome)]];
    rows.push(["Action requested", readableCode(plannedAction.action)]);
    rows.push(["What happened", String(result.message ?? "The controlled automation result was recorded.")]);
    if (result.outcome === "HUMAN_REVIEW_TASK_CREATED") {
      rows.push(["Review queue", "The task is now open for a human reviewer."]);
      rows.push(["Review type", readableCode(eventPayload.task_type || "DATA_QUALITY")]);
      rows.push(["Priority", readableCode(eventPayload.priority || "MEDIUM")]);
    } else if (result.outcome === "DRY_RUN_NOT_EXECUTED") {
      rows.push(["Review queue", "No review task was created by this action."]);
      rows.push(["Next step", "Use a CREATE_REVIEW_TASK rule when a human review task is required."]);
    }
    return rows;
  })() : null;
  const resultRows = automationResultRows ?? (result ? Object.entries(result).flatMap(([key, value]) => {
    if (key === "metrics" && value && typeof value === "object") return Object.entries(value as Record<string, unknown>).map(([metric, metricValue]) => [`Verified ${metric.replaceAll("_", " ")}`, typeof metricValue === "object" ? JSON.stringify(metricValue) : String(metricValue)]);
    if (key === "reconciliation_summary" && value && typeof value === "object") return Object.entries(value as Record<string, unknown>).filter(([summaryKey]) => summaryKey !== "items").map(([summaryKey, summaryValue]) => [summaryKey.replaceAll("_", " "), String(summaryValue)]);
    if (key === "signals" && Array.isArray(value)) return [["Signals created", String(value.length)]];
    return [[key.replaceAll("_", " "), typeof value === "object" ? JSON.stringify(value) : String(value)]];
  }) : []);
  return <div className="row g-3">
    {canExecuteRules && <div className="col-12"><Panel title="Configure automation rule"><p className="form-text">Create a rule that connects an event to a controlled action. CREATE_REVIEW_TASK creates a human review task; other actions remain dry runs.</p><form className="row g-2" onSubmit={createRule}><div className="col-md-3"><SelectField label="Program" name="rule-program" options={programs.map((program) => ({ value: program.id, label: programLabel(program) }))} /></div><div className="col-md-3"><SelectField label="Event" name="event_name" options={["PAYMENT_FAILED", "DUPLICATE_FLAG", "HIGH_SEVERITY_COMPLAINT", "STALLED_REVIEW"].map((value) => ({ value, label: value }))} required /></div><div className="col-md-3"><SelectField label="Action" name="action_name" options={["CREATE_REVIEW_TASK", "RETRY", "ESCALATE", "REMINDER"].map((value) => ({ value, label: value }))} required /></div><div className="col-md-2"><Field label="Priority" name="rule_priority" type="number" defaultValue="100" required /></div><div className="col-md-1 d-flex align-items-end"><div className="form-check mb-3"><input className="form-check-input" id="rule-active" name="rule_active" type="checkbox" defaultChecked /><label className="form-check-label" htmlFor="rule-active">Active</label></div></div><div className="col-12"><button className="btn btn-outline-primary">Save automation rule</button></div></form><Message status={status} error={error} /></Panel></div>}
    <div className="col-12"><Panel title="AI and automation status"><div className="alert alert-secondary"><strong>{data?.status ?? "Loading"}.</strong> {data?.message ?? "Loading advisory service status."}</div><p><strong>Advisory AI signals:</strong> {data?.signals_count ?? 0} <span className="ms-3"><strong>Open human reviews:</strong> {data?.review_tasks_open ?? 0}</span></p><Table headings={["Configured event", "Controlled action", "Priority", "Status"]} rows={(data?.rules ?? []).map((rule) => [valueOf(rule, "event_name"), valueOf(rule, "action_name"), valueOf(rule, "priority"), <Badge value={valueOf(rule, "is_active") === "true" ? "ACTIVE" : "DISABLED"} />])} /></Panel></div>
    {canInspectPipeline && <div className="col-12"><Panel title="Data pipeline status"><Table headings={["Pipeline stage", "Status"]} rows={Array.isArray(pipeline?.pipeline) ? (pipeline.pipeline as string[]).map((stage) => [stage.replaceAll("_", " "), String(pipeline?.status ?? "completed")]) : []} /><p className="form-text"><span>{String((pipeline?.records as Record<string, unknown> | undefined)?.beneficiaries ?? 0)}</span> beneficiary records, <span>{String((pipeline?.records as Record<string, unknown> | undefined)?.signals ?? 0)}</span> advisory signals, and <span>{String((pipeline?.records as Record<string, unknown> | undefined)?.review_tasks ?? 0)}</span> review tasks are in the permitted scope.</p></Panel></div>}
    {canDeduplicate && <div className="col-lg-6"><Panel title="Duplicate review"><FunctionHelp><p>Choose a beneficiary to compare with other beneficiaries in the same authorized program. The result can create an advisory signal and a human review task; it never deletes or approves records.</p></FunctionHelp><form onSubmit={(event) => { const form = new FormData(event.currentTarget); void run(event, `/ai/deduplicate/${String(form.get("beneficiary"))}/`, () => ({})); }}><SelectField label="Beneficiary" name="beneficiary" options={beneficiaries.map((item) => ({ value: item.id, label: `${beneficiaryLabel(item)} — ${valueOf(item, "program_name")}` }))} required /><p className="form-text">The detector creates advisory signals and human review tasks only.</p><button className="btn btn-outline-primary">Run duplicate check</button></form></Panel></div>}
    {canReconcile && <><div className="col-lg-6"><Panel title="Advisory risk review"><FunctionHelp><p>Choose a simulated payment batch. The scan creates advisory risk signals for human review and does not change payment status.</p></FunctionHelp><form onSubmit={(event) => run(event, "/ai/anomalies/", (form) => ({ batch_id: String(form.get("batch")) }))}><SelectField label="Simulated payment batch" name="batch" options={batches.map((batch) => ({ value: batch.id, label: `${batch.name ?? "Batch"} — ${valueOf(batch, "program_name")}` }))} required /><button className="btn btn-outline-primary">Run advisory risk scan</button></form></Panel></div><div className="col-lg-6"><Panel title="Simulated reconciliation"><FunctionHelp><p>Choose a payment batch and enter the simulated provider report as JSON. Each reference needs an amount and status. The result identifies matched payments and exceptions for human resolution.</p></FunctionHelp><form onSubmit={(event) => run(event, "/ai/reconcile/", (form) => { const raw = String(form.get("provider_report_data") || "").trim(); try { const parsed = JSON.parse(raw); if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error(); return { batch_id: String(form.get("batch")), provider_report_data: parsed }; } catch { throw new Error("Provider report must be a JSON object. Use double quotes and include amount and status for each provider reference."); } })}><SelectField label="Simulated payment batch" name="batch" options={batches.map((batch) => ({ value: batch.id, label: `${batch.name ?? "Batch"} — ${valueOf(batch, "program_name")}` }))} required /><Textarea label="Controlled simulated provider report" name="provider_report_data" required placeholder='{"SIM-reference":{"amount":"100","status":"SUCCESS"}}' help='Use JSON only: each provider reference must contain amount and status.' /><button className="btn btn-outline-primary">Reconcile simulated batch</button></form></Panel></div></>}
    {canUseCopilot && <div className="col-lg-6"><Panel title="AI reporting copilot"><FunctionHelp><p>Choose a program and ask for verified beneficiaries, approvals, payments, failures, complaints, or budget figures. The result is a verified metrics draft for people to interpret; it does not make decisions.</p></FunctionHelp><form onSubmit={(event) => run(event, "/ai/copilot/", (form) => ({ program_id: String(form.get("program")), question: String(form.get("question")) }))}><SelectField label="Program" name="program" options={programs.map((program) => ({ value: program.id, label: programLabel(program) }))} required /><Textarea label="Reporting question" name="question" required /><button className="btn btn-outline-primary">Get verified draft</button></form></Panel></div>}
    {canExecuteRules && <div className="col-lg-6"><Panel title="Execute controlled automation rule"><FunctionHelp><p>Choose an active rule. CREATE_REVIEW_TASK adds a human task to the queue. RETRY, ESCALATE, and REMINDER are recorded as dry runs only, so they do not change payment or eligibility.</p></FunctionHelp><form onSubmit={(event) => run(event, "/ai/automation/execute/", (form) => { const selectedRule = activeRules.find((rule) => rule.id === String(form.get("rule"))); const action = String(selectedRule?.action_name ?? ""); return { rule_id: String(form.get("rule")), idempotency_key: crypto.randomUUID(), event_payload: action === "CREATE_REVIEW_TASK" ? { task_type: String(form.get("task_type")), priority: String(form.get("priority")) } : {} }; })}><SelectField label="Active rule" name="rule" options={activeRules.map((rule) => ({ value: rule.id, label: `${valueOf(rule, "event_name")} → ${valueOf(rule, "action_name")}` }))} value={activeRules.some((rule) => rule.id === selectedAutomationAction) ? selectedAutomationAction : undefined} onChange={(ruleId) => { setSelectedAutomationAction(ruleId); }} required />{(activeRules.find((rule) => rule.id === selectedAutomationAction)?.action_name ?? "") === "CREATE_REVIEW_TASK" && <><SelectField label="Human review type" name="task_type" options={["DATA_QUALITY", "DUPLICATE_REVIEW", "RISK_REVIEW", "ELIGIBILITY_REVIEW", "PAYMENT_EXCEPTION", "RECONCILIATION", "COMPLAINT_ESCALATION"].map((value) => ({ value, label: value }))} required /><SelectField label="Priority" name="priority" options={["LOW", "MEDIUM", "HIGH", "CRITICAL"].map((value) => ({ value, label: value }))} required /></>}<p className="form-text">A CREATE_REVIEW_TASK rule adds work to the human review queue. RETRY, ESCALATE, and REMINDER are recorded as dry runs and do not change payment or eligibility.</p><button className="btn btn-outline-primary" disabled={!activeRules.length}>Execute controlled rule</button></form></Panel></div>}
    {canDeduplicate && <div className="col-lg-6"><Panel title="Record advisory signal review"><FunctionHelp><p>Select an open signal created by duplicate or risk detection, choose the human outcome, and record the evidence or decision note. This closes the signal; it does not automatically change eligibility or payment.</p></FunctionHelp><form onSubmit={(event) => { const form = new FormData(event.currentTarget); void run(event, `/ai-signals/${String(form.get("signal"))}/review/`, (submittedForm) => ({ status: String(submittedForm.get("signal_status")), review_note: String(submittedForm.get("review_note")) })); }}><SelectField label="Open advisory signal" name="signal" options={openSignals.map((signal) => ({ value: signal.id, label: `${valueOf(signal, "signal_type")} — ${valueOf(signal, "program_name") || "Tenant"}` }))} required /><SelectField label="Human review outcome" name="signal_status" options={["REVIEWED", "ACCEPTED", "DISMISSED"].map((value) => ({ value, label: value }))} required /><Textarea label="Review note" name="review_note" required /><button className="btn btn-outline-primary" disabled={!openSignals.length}>Save human review</button></form></Panel></div>}
    {canDeduplicate && <div className="col-lg-6"><Panel title="Resolve review task"><FunctionHelp><p>Select an open task, review the related records, and write what was decided or what follow-up is required. Resolving the task records the human action in the audit history.</p></FunctionHelp><form onSubmit={(event) => { const form = new FormData(event.currentTarget); void run(event, `/review-tasks/${String(form.get("review_task"))}/resolve/`, (submittedForm) => ({ resolution: String(submittedForm.get("task_resolution")) })); }}><SelectField label="Open review task" name="review_task" options={openReviewTasks.map((task) => ({ value: task.id, label: `${valueOf(task, "task_type")} — ${valueOf(task, "program_name") || "Tenant"}` }))} required /><Textarea label="Resolution note" name="task_resolution" required /><button className="btn btn-outline-primary" disabled={!openReviewTasks.length}>Resolve task</button></form></Panel></div>}
    {canResolveReconciliation && <div className="col-lg-6"><Panel title="Resolve reconciliation exception"><FunctionHelp><p>Select an open exception, compare the expected and provider values, then record how the mismatch was resolved. The item remains an auditable human resolution.</p></FunctionHelp><form onSubmit={(event) => { const form = new FormData(event.currentTarget); void run(event, `/reconciliation-items/${String(form.get("reconciliation_item"))}/resolve/`, (submittedForm) => ({ resolution_note: String(submittedForm.get("resolution_note")) })); }}><SelectField label="Open reconciliation exception" name="reconciliation_item" options={unresolvedReconciliationItems.map((item) => ({ value: item.id, label: `${valueOf(item, "issue_type")} — ${valueOf(item, "program_name") || "Program"}` }))} required /><Textarea label="Resolution note" name="resolution_note" required /><button className="btn btn-outline-primary" disabled={!unresolvedReconciliationItems.length}>Resolve reconciliation item</button></form></Panel></div>}
    {canDeduplicate && <div className="col-lg-6"><Panel title="Human review queue"><Table headings={["Type", "Program", "Priority", "Status", "Assigned to"]} rows={reviewTasks.map((task) => [valueOf(task, "task_type"), valueOf(task, "program_name") || "Tenant", <Badge value={valueOf(task, "priority") || "MEDIUM"} />, <Badge value={task.status ?? "OPEN"} />, valueOf(task, "assigned_to_name") || "Unassigned"])} /></Panel></div>}
    {canDeduplicate && <div className="col-12"><Panel title="Advisory AI signals"><Table headings={["Type", "Program", "Confidence", "Status", "Reason"]} rows={signals.map((signal) => [valueOf(signal, "signal_type"), valueOf(signal, "program_name") || "Tenant", valueOf(signal, "confidence"), <Badge value={signal.status ?? "OPEN"} />, valueOf(signal, "reason")])} /></Panel></div>}
    {canReconcile && <div className="col-12"><Panel title="Reconciliation exceptions"><Table headings={["Program", "Issue", "Expected", "Actual", "Status", "Resolution"]} rows={reconciliationItems.map((item) => [valueOf(item, "program_name") || "Program", valueOf(item, "issue_type"), valueOf(item, "expected_amount") || "—", valueOf(item, "actual_amount") || "—", <Badge value={item.status ?? "OPEN"} />, valueOf(item, "resolution_note") || "Pending human review"])} /></Panel></div>}
    {result && <div className="col-12"><Panel title="Controlled service result"><p className="form-text">This is the outcome of the action you just ran. Review the values below and use any new signal, task, or reconciliation exception in the relevant queue.</p><Table headings={["Result", "Value"]} rows={resultRows} /></Panel></div>}
    <div className="col-12"><Message status={status} error={error} /></div>
  </div>;
}

function UsersPage({ system }: { system: boolean }) {
  const [users, setUsers] = useState<PlatformUser[]>([]); const [tenants, setTenants] = useState<Entity[]>([]); const [status, setStatus] = useState(""); const [error, setError] = useState("");
  const roles: Role[] = system ? ["ADMIN", "FIELD_OFFICER", "FINANCE", "REVIEWER", "SUPPORT", "MANAGER", "AUDITOR"] : ["FIELD_OFFICER", "FINANCE", "REVIEWER", "SUPPORT", "AUDITOR"];
  const load = async () => { try { setUsers(listItems(await api.users())); if (system) setTenants(listItems(await api.list<Entity>("tenants"))); } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not load users."); } };
  useEffect(() => { void load(); }, [system]);
  const submit = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const formElement = event.currentTarget; const form = new FormData(formElement); setStatus("saving"); setError(""); try { await api.createUser({ full_name: String(form.get("full_name")), email: String(form.get("email")), role: String(form.get("role")) as Role, password: String(form.get("password")), ...(system ? { tenant_id: String(form.get("tenant")) } : {}) }); formElement.reset(); await load(); setStatus("saved"); } catch (reason) { setStatus("failed"); setError(reason instanceof Error ? reason.message : "Could not create the user."); } };
  const toggleActive = async (person: PlatformUser) => { try { await api.update("users", person.id, { is_active: !person.is_active }); await load(); } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not update user access."); } };
  return <div className="row g-3"><div className="col-lg-7"><Panel title={system ? "All tenant users" : "Tenant users"}><Table headings={["Name", "Email", "Tenant", "Role", "Status", "Access"]} rows={users.map((person) => [person.full_name, person.email, person.tenant?.name ?? "Current tenant", person.role, <Badge value={person.is_active ? "ACTIVE" : "INACTIVE"} />, <button className="btn btn-sm btn-outline-secondary" onClick={() => void toggleActive(person)}>{person.is_active ? "Deactivate" : "Activate"}</button>])} /></Panel></div><div className="col-lg-5"><Panel title="Add tenant user"><form onSubmit={submit}>{system && <SelectField label="Tenant" name="tenant" options={tenants.map((tenant) => ({ value: tenant.id, label: tenant.name ?? "Unnamed tenant" }))} required />}<Field label="Full name" name="full_name" required /><Field label="Work email" name="email" type="email" required /><SelectField label="Role" name="role" options={roles.map((value) => ({ value, label: value }))} required /><Field label="Temporary password" name="password" type="password" required /><button className="btn btn-primary">Create user</button></form><Message status={status} error={error} /></Panel></div></div>;
}

function AuditPage() {
  const [events, setEvents] = useState<Entity[]>([]); const [error, setError] = useState("");
  useEffect(() => { api.list<Entity>("audit-events").then((response) => setEvents(listItems(response))).catch((reason) => setError(reason instanceof Error ? reason.message : "Could not load permitted audit history.")); }, []);
  return <Panel title="Audit history"><Message status="" error={error} /><Table headings={["Action", "Entity", "Actor", "Time"]} rows={events.map((event) => [valueOf(event, "action"), valueOf(event, "entity_type"), valueOf(event, "actor_name") || "System", valueOf(event, "created_at")])} /></Panel>;
}

function ProfilePage({ user, onUpdated }: { user: PlatformUser; onUpdated: (user: PlatformUser) => void }) {
  const [detailsStatus, setDetailsStatus] = useState("");
  const [passwordStatus, setPasswordStatus] = useState("");
  const [detailsError, setDetailsError] = useState("");
  const [passwordError, setPasswordError] = useState("");
  const updateDetails = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setDetailsStatus("saving"); setDetailsError("");
    try {
      const updated = await api.updateProfile({ full_name: String(form.get("profile_name")), email: String(form.get("profile_email")), current_password: String(form.get("details_password")) });
      onUpdated(updated); setDetailsStatus("saved");
    } catch (reason) { setDetailsStatus("failed"); setDetailsError(reason instanceof Error ? reason.message : "Could not update your profile details."); }
  };
  const updatePassword = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setPasswordStatus("saving"); setPasswordError("");
    try {
      await api.changePassword({ current_password: String(form.get("old_password")), new_password: String(form.get("new_password")), confirm_password: String(form.get("confirm_password")) });
      event.currentTarget.reset(); setPasswordStatus("saved");
    } catch (reason) { setPasswordStatus("failed"); setPasswordError(reason instanceof Error ? reason.message : "Could not change your password."); }
  };
  return <div className="profile-grid"><Panel title="Update personal details"><p className="section-lead">Change the name and email shown on your H-CAP account.</p><form onSubmit={updateDetails}><Field label="Full name" name="profile_name" defaultValue={user.full_name} required /><Field label="Work email" name="profile_email" type="email" defaultValue={user.email} required /><Field label="Current password" name="details_password" type="password" required /><button className="btn btn-primary">Save details</button><Message status={detailsStatus} error={detailsError} /></form></Panel><Panel title="Change password"><p className="section-lead">Use your current password to set a new password for this account.</p><form onSubmit={updatePassword}><Field label="Current password" name="old_password" type="password" required /><Field label="New password" name="new_password" type="password" required help="Use at least 8 characters." /><Field label="Confirm new password" name="confirm_password" type="password" required /><button className="btn btn-primary">Update password</button><Message status={passwordStatus} error={passwordError} /></form></Panel></div>;
}

function App() {
  const [user, setUser] = useState<PlatformUser | null>(null); const [locale, setLocale] = useState<Locale>("en"); const [route, setRoute] = useState<Route>(routeFromHash);
  const translate = useMemo(() => (value: string) => locale === "ar" ? translateArabic(value) : value, [locale]);
  useEffect(() => { const update = () => setRoute(routeFromHash()); addEventListener("hashchange", update); if (localStorage.getItem("hcap_token")) api.me().then(setUser).catch(api.clearToken); return () => removeEventListener("hashchange", update); }, []);
  useEffect(() => { document.documentElement.lang = locale; document.documentElement.dir = locale === "ar" ? "rtl" : "ltr"; }, [locale]);
  useEffect(() => { if (user && !roleRoutes[route].includes(user.role as Role)) location.hash = "#/dashboard"; }, [route, user]);
  if (!user) return <TranslationContext.Provider value={translate}><Login onLogin={(staff) => { setUser(staff); location.hash = "#/dashboard"; }} /></TranslationContext.Provider>;
  const role = user.role as Role; const go = (target: Route) => { location.hash = `#/${routePaths[target]}`; };
  const content = route === "dashboard" ? <Dashboard user={user} /> : route === "tenants" ? <TenantsPage /> : route === "programs" ? <ProgramsPage role={role} /> : route === "households" ? <Intake beneficiary={false} role={role} /> : route === "beneficiaries" ? <Intake beneficiary role={role} /> : route === "eligibility" ? <EligibilityPage /> : route === "enrollment" ? <EnrollmentDecisionPage /> : route === "payments" ? <PaymentsPage role={role} /> : route === "pdm" ? <PdmPage user={user} /> : route === "complaints" ? <ComplaintsPage role={role} /> : route === "budgets" ? <BudgetsPage role={role} /> : route === "activities" ? <ActivitiesPage role={role} /> : route === "imports" ? <ImportsPage /> : route === "automation" ? <AutomationPage role={role} /> : route === "users" ? <UsersPage system={user.is_superuser} /> : route === "profile" ? <ProfilePage user={user} onUpdated={setUser} /> : <AuditPage />;
  const signOut = async () => { try { await api.logout(); } finally { api.clearToken(); setUser(null); } };
  const title = translate("Humanitarian Cash Assistance Platform");
  const subtitle = translate("Secure, accountable assistance delivery");
  return <TranslationContext.Provider value={translate}><TranslateTree><div dir={locale === "ar" ? "rtl" : "ltr"}><a className="skip-link" href="#main">Skip to content</a><aside className="sidebar"><div className="agency-lockup"><div className="agency-mark" aria-hidden="true">+</div><div><strong>H-CAP</strong><small>Humanitarian operations</small></div></div><nav aria-label={translate("Primary navigation")}>{nav.filter(([item]) => roleRoutes[item].includes(role)).map(([item, label, Icon]) => <button className={`nav-item ${route === item ? "active" : ""}`} key={item} onClick={() => go(item)}><Icon size={17} /> {translate(label)}</button>)}</nav><div className="sidebar-footer"><strong>{user.full_name}</strong><small>{translate(user.role)}</small></div></aside><main className="workspace" id="main"><header className="topbar"><div><p className="eyebrow">{subtitle}</p><h1>{title}</h1></div><div className="topbar-actions"><button className="btn btn-outline-primary btn-sm" onClick={() => go("profile")}><UserCog size={16} /> {translate("Edit profile")}</button><button className="btn btn-outline-primary btn-sm" onClick={() => setLocale(locale === "en" ? "ar" : "en")}><Languages size={16} /> {locale === "en" ? "العربية" : "English"}</button><button className="btn btn-outline-primary btn-sm" onClick={() => void signOut()}>{translate("Sign out")}</button></div></header>{content}</main></div></TranslateTree></TranslationContext.Provider>;
}

createRoot(document.getElementById("root")!).render(<App />);
