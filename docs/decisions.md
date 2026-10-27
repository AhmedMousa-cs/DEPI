# NEXUS: القرارات الثابتة

التاريخ: ____  |  الفريق: ____

القرارات دي اتفقنا عليها ومابتتغيرش من غير سبب مكتوب هنا. اللي عليه "افتراض" لسه محتاج تأكيد.

| الموضوع | القرار | ملاحظة |
|---|---|---|
| لغة الـbackend | Python 3.11+ | |
| الـAPI | FastAPI + Uvicorn | |
| الداتابيز | PostgreSQL من أول يوم (صورة فيها pgvector) | مش SQLite: عشان PostgresSaver والـpgvector وDocker |
| الـORM والـmigrations | SQLAlchemy 2 + Alembic | |
| الـValidation | Pydantic v2 | كل مخرج من agent لازم يطابق schema |
| الـAgents | LangChain v1 (create_agent + middleware) | بس في الأماكن المحتاجة loop tools |
| الـOrchestration | LangGraph (StateGraph + interrupt() + Command(resume=...)) | graph واحد، instance لكل مشروع |
| الـCheckpointer | PostgresSaver | MemorySaver للاختبارات السريعة بس |
| الـLLM | Anthropic عن طريق langchain-anthropic | افتراض: معزول في llm/factory.py، واسم الموديل في env |
| الـObservability | Langfuse Cloud (free tier) | self-host اختياري لاحقاً |
| الـEvals | DeepEval (pytest) + اختبارات حتمية | |
| الـBackground jobs | جدول jobs + worker process + polling | من غير Celery/Redis |
| الملفات المدعومة | txt وmd وdocx وpdf (نصي) | الحد الأقصى 5MB، من غير OCR |
| الـAuth | JWT + حسابات seed، أدوار PM وTEAM_LEADER | من غير تسجيل ذاتي |
| الـfront-end | نكمل على الموجود ونوصله بالـAPI | القرار النهائي (نكمل ولا React) في آخر M2 |
| الـDeployment | Docker Compose (db + api + worker + frontend) | على جهاز واحد |
| الـLLM budget | حد أقصى للـtokens لكل job + حد يومي بالدولار في env | |

## قواعد المعمارية (مش قابلة للتفاوض)

1. الـDB هي المرجع. حالة الـgraph مؤقتة (مكان تقدم التشغيل).
2. الـAgent يقترح بس: بيكتب drafts من خلال دوال save_draft_*. الاعتماد من endpoints محمية بدور PM.
3. كل عنصر مستخرج معاه: source_id + quote + run_id + created_by_agent + status + confidence.
4. الـquote بيتفحص بالكود: لو مش موجود حرفياً في المصدر، العنصر بيتعلّم unverified وثقته بتنزل.
5. الـGlobal Supervisor ما بيغيّرش حاجة، بيكتب recommendations بس.
6. أي تغيير يتطبق على الخطة بيعدي على PM، ويعمل plan_version جديد وaudit_log.

## خارج النطاق

OCR، تسجيل/تفريغ الاجتماعات، إرسال رسائل خارجية تلقائية، agent مستقل لكل مشروع، تحسين موارد آلي، multi-tenant حقيقي، email notifications.

## سجل القرارات

| التاريخ | القرار | السبب |
|---|---|---|
| | | |