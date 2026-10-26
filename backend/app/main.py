import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from .config import Settings, get_settings
from .documents import audio_duration, extracted_pages, inspect_file, prepare_document
from .domain import (COVERAGE, checkpoint, compare_documents, new_case, public_case, reconciliation,
                     require_revision, source_map, validate_assessment, validate_citations)
from .gateway import Gateway
from .limits import RequestBodyLimit
from .repository import Repository, now, uid
from .schemas import (AnalysisInput, Answer, Assessment, CompareInput, ConfirmInput, CreateCase, DraftEdit,
                      DraftInput, DraftOutput, LanguageInput, LedgerInput, MessageInput, OutputInput, ReviewInput)


def create_app(settings: Settings | None = None, gateway=None) -> FastAPI:
    settings = settings or get_settings()
    repo = Repository(settings)
    ai = gateway or Gateway(settings)
    app = FastAPI(title='Eagle Eye', version='0.1.0', docs_url='/docs' if settings.app_env != 'production' else None,
                  redoc_url=None, openapi_url='/openapi.json' if settings.app_env != 'production' else None)
    app.state.repo = repo
    app.state.gateway = ai
    app.add_middleware(RequestBodyLimit, max_bytes=settings.max_request_bytes)
    app.add_middleware(CORSMiddleware, allow_origins=settings.origins, allow_credentials=False,
                       allow_methods=['GET', 'POST', 'PATCH', 'DELETE'], allow_headers=['Authorization', 'Content-Type'])

    @app.middleware('http')
    async def headers(request: Request, call_next):
        length = request.headers.get('content-length', '0')
        if not length.isdigit() or int(length) > settings.max_request_bytes:
            return JSONResponse({'error': {'message': 'Request too large.'}}, status_code=413)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Permissions-Policy'] = 'microphone=(self), camera=(self)'
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException):
        return JSONResponse({'error': {'message': error.detail, 'status': error.status_code}}, status_code=error.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        # Do not echo input, tokens, transcript contents or provider bodies.
        fields = ['.'.join(str(v) for v in e['loc']) for e in error.errors()]
        return JSONResponse({'error': {'message': 'Check the required fields and supported values.', 'fields': fields}}, status_code=422)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception):
        return JSONResponse({'error': {'message': 'The operation could not finish. Reload to check saved progress before retrying.'}}, status_code=500)

    async def session(authorization: Annotated[str | None, Header()] = None) -> tuple[str, str]:
        if not authorization or not authorization.startswith('Bearer '):
            raise HTTPException(401, 'Sign in or start a local session first.')
        token = authorization[7:]
        return await repo.authenticate(token), token

    Session = Annotated[tuple[str, str], Depends(session)]

    async def get_case(case_id, auth, deleted=False):
        return await repo.get(str(case_id), *auth, allow_deleted=deleted)

    async def save(case, auth, deleted=False):
        await repo.save(case, *auth, allow_deleted=deleted)

    async def paid(auth, category='requests', amount=1):
        limits = {'requests': (settings.max_requests_per_user_per_hour, 3600),
                  'pages': (settings.max_document_pages_per_user_per_day, 86400),
                  'audio': (settings.max_audio_seconds_per_user_per_day, 86400)}
        await repo.consume(*auth, category, amount, *limits[category])

    def doc_by_id(case, document_id):
        document = next((d for d in case['documents'] if d['id'] == str(document_id)), None)
        if document is None:
            raise HTTPException(404, 'Document not found in this case.')
        return document

    async def begin(case, body, auth, kind):
        key = str(body.request_id)
        fingerprint = hashlib.sha256((kind + body.model_dump_json()).encode()).hexdigest()
        existing = case['operations'].get(key)
        if existing:
            if existing['fingerprint'] != fingerprint:
                raise HTTPException(409, 'This request ID was already used for different input.')
            if existing['status'] == 'complete':
                return False
            raise HTTPException(409, 'This request already started. Reload to inspect progress, then explicitly retry with a new request.')
        require_revision(case, body.expected_revision)
        case['operations'][key] = {'fingerprint': fingerprint, 'status': 'started', 'kind': kind, 'at': now()}
        await save(case, auth)
        return True

    def finish(case, body):
        case['operations'][str(body.request_id)]['status'] = 'complete'

    def context(case, question='', document_id=None):
        sources = source_map(case, document_id)
        # Bound context using whole pages when possible; always disclose truncation.
        ranked = sorted(sources.values(), key=lambda s: sum(word.casefold() in s['text'].casefold() for word in question.split()), reverse=True)
        evidence, budget = [], 32000
        for source in ranked:
            if budget <= 0:
                break
            text = source['text'][:min(6000, budget)]
            evidence.append({**source, 'text': text, 'excerpt_truncated': len(text) < len(source['text'])})
            budget -= len(text)
        return {'language': case['language'], 'domain': case['domain'], 'facts': case['facts'],
                'reconciliation': reconciliation(case['ledger']), 'evidence': evidence,
                'documents': [{'id': d['id'], 'version': d['version'], 'complete': d['complete'], 'status': d['status']} for d in case['documents']],
                'question': question, 'recent_conversation': [{'role': m['role'], 'text': m['text']} for m in case['messages'][-6:]], 'coverage': COVERAGE}, sources

    def output(case, text, kind, citations=None):
        item = {'id': uid(), 'text': text, 'kind': kind, 'language': case['language'], 'revision': case['revision'], 'citations': citations or [], 'created_at': now()}
        case['outputs'].append(item)
        return item['id']

    @app.get('/healthz')
    async def health():
        return {'status': 'ok', 'service': 'eagle-eye'}

    @app.get('/readyz')
    async def ready():
        return {'storage_mode': settings.storage_mode, 'ai_configured': bool(settings.sarvam_api_key.get_secret_value()),
                'ai_enabled': settings.ai_enabled, 'live_probe': False}

    @app.get('/api/capabilities')
    async def capabilities():
        return {'storage_mode': settings.storage_mode, 'languages': ['en-IN', 'hi-IN'], 'max_upload_mb': settings.max_upload_mb,
                'max_document_pages': settings.max_document_pages, 'max_audio_seconds': settings.max_audio_seconds,
                'max_upload_batch_mb': settings.max_upload_batch_mb,
                'supabase_url': settings.supabase_url if settings.storage_mode == 'supabase' else None,
                'supabase_publishable_key': settings.supabase_publishable_key if settings.storage_mode == 'supabase' else None,
                'formats': ['application/pdf', 'image/jpeg', 'image/png', 'image/webp'],
                'ai_configured': bool(settings.sarvam_api_key.get_secret_value()), 'coverage': COVERAGE,
                'professional_review': False, 'legal_source_coverage': 'Official help navigation only; no admitted employment-law pack.',
                'help': [{'title': 'NALSA legal aid: eligibility and application information', 'url': 'https://nalsa.gov.in/legal-aid/'}]}

    @app.post('/api/auth/local')
    async def local_auth(request: Request):
        if settings.storage_mode != 'local' or settings.app_env == 'production':
            raise HTTPException(404, 'Local sessions are disabled.')
        if request.client and request.client.host not in ('127.0.0.1', '::1', 'localhost', 'testclient'):
            raise HTTPException(403, 'Local development sessions require loopback access.')
        origin = request.headers.get('origin')
        if origin and origin not in settings.origins:
            raise HTTPException(403, 'Origin is not permitted.')
        await repo.consume('local-session-creation', '', 'sessions', 1, 60, 3600)
        return repo.local_session()

    @app.get('/api/cases')
    async def list_cases(auth: Session):
        cases = await repo.list(*auth)
        return [{'id': c['id'], 'title': c['title'], 'domain': c['domain'], 'language': c['language'],
                 'revision': c['revision'], 'updated_at': c['updated_at'], 'documents': len(c['documents'])} for c in cases]

    @app.post('/api/cases', status_code=201)
    async def create_case(body: CreateCase, auth: Session):
        if not body.consent:
            raise HTTPException(422, 'Confirm processing consent before creating a live case.')
        await repo.consume(*auth, 'case_creation', 1, 30, 86400)
        case = new_case(body.model_dump())
        await repo.create(case, *auth)
        return public_case(case)

    @app.get('/api/cases/{case_id}')
    async def read_case(case_id: UUID, auth: Session):
        return public_case(await get_case(case_id, auth))

    @app.patch('/api/cases/{case_id}/language')
    async def language(case_id: UUID, body: LanguageInput, auth: Session):
        case = await get_case(case_id, auth)
        case['language'] = body.language
        await save(case, auth)
        return public_case(case)

    @app.post('/api/cases/{case_id}/messages')
    async def message(case_id: UUID, body: MessageInput, auth: Session):
        case = await get_case(case_id, auth)
        if body.document_id:
            doc_by_id(case, body.document_id)
        if body.transcript_id and not any(t['id'] == str(body.transcript_id) for t in case['transcripts']):
            raise HTTPException(404, 'Transcript not found in this case.')
        if not await begin(case, body, auth, 'message'):
            return public_case(case)
        await paid(auth)
        user_message = {'id': uid(), 'role': 'user', 'text': body.text, 'at': now(), 'transcript_id': str(body.transcript_id) if body.transcript_id else None}
        case['messages'].append(user_message)
        await save(case, auth)
        ctx, sources = context(case, body.text, str(body.document_id) if body.document_id else None)
        answer, metrics = await ai.complete('Answer the question or help with intake. Propose facts only; ask missing role, location, document status, goal, worker category, dates/pay basis at most two at a time. Use fact keys role, location, document_status, goal, worker_category where applicable. Facts must quote supplied evidence and identify its source_id.', ctx, Answer)
        result = answer.model_dump()
        result['citations'] = validate_citations(result['citations'], sources)
        result['facts'] = [fact for fact in result['facts'] if fact['source_id'] in sources and validate_citations([{'source_id': fact['source_id'], 'quote': fact['quote']}], sources)]
        if not await ai.check_support(json.dumps(result, ensure_ascii=False), ctx['evidence'] + [{'confirmed_facts': case['facts'], 'reconciliation': ctx['reconciliation']}]):
            raise HTTPException(502, 'The answer failed the evidence check. Your question is saved; try a narrower question.')
        if result['facts']:
            case['proposals'].append({'id': uid(), 'facts': result['facts'], 'revision': case['revision'], 'status': 'pending', 'at': now()})
        out_id = output(case, result['answer'], 'answer', result['citations'])
        case['messages'].append({'id': uid(), 'role': 'assistant', 'text': result['answer'], 'at': now(),
                                 'questions': result['questions'], 'gaps': result['gaps'], 'citations': result['citations'], 'output_id': out_id, 'metrics': metrics})
        finish(case, body)
        await save(case, auth)
        return public_case(case)

    @app.post('/api/cases/{case_id}/confirm')
    async def confirm(case_id: UUID, body: ConfirmInput, auth: Session):
        case = await get_case(case_id, auth)
        if not await begin(case, body, auth, 'confirm'):
            return public_case(case)
        sources = source_map(case)
        for fact in body.facts:
            if fact.source_id and fact.source_id not in sources:
                raise HTTPException(422, 'A fact refers to evidence outside this case or to unreviewed text.')
            if fact.source_id and fact.quote and not validate_citations([{'source_id': fact.source_id, 'quote': fact.quote}], sources):
                raise HTTPException(422, 'A quoted passage does not match the selected evidence.')
        proposal = None
        if body.proposal_id:
            proposal = next((p for p in case['proposals'] if p['id'] == str(body.proposal_id)), None)
            if proposal is None or proposal['revision'] != case['revision']:
                raise HTTPException(409, 'This proposal is missing or based on an older revision.')
        checkpoint(case, 'User confirmed or corrected facts')
        for fact in body.facts:
            item = fact.model_dump()
            item['status'] = 'unknown' if not item['value'].strip() else ('disputed' if fact.status == 'disputed' else 'user_confirmed')
            item['confirmed_at'] = now()
            # Preserve disputed claims alongside the user's current account.
            if item['status'] != 'disputed':
                case['facts'] = [f for f in case['facts'] if f['key'] != item['key'] or f['status'] == 'disputed']
            case['facts'].append(item)
        if proposal:
            proposal['status'] = 'confirmed'
        finish(case, body)
        await save(case, auth)
        return public_case(case)

    @app.post('/api/cases/{case_id}/ledger')
    async def ledger(case_id: UUID, body: LedgerInput, auth: Session):
        case = await get_case(case_id, auth)
        if not await begin(case, body, auth, 'ledger'):
            return public_case(case)
        checkpoint(case, 'User confirmed salary reconciliation')
        case['ledger'] = body.model_dump(mode='json', exclude={'request_id', 'expected_revision'})
        finish(case, body)
        await save(case, auth)
        return public_case(case)

    @app.post('/api/cases/{case_id}/documents')
    async def upload(case_id: UUID, auth: Session, files: Annotated[list[UploadFile], File()],
                     manifest: Annotated[str, Form()], request_id: Annotated[UUID, Form()],
                     expected_revision: Annotated[int, Form()], complete: Annotated[bool, Form()] = False,
                     previous_document_id: Annotated[UUID | None, Form()] = None):
        case = await get_case(case_id, auth)
        if len(files) > settings.max_document_pages or len(manifest) > 12000:
            raise HTTPException(413, 'Too many files or page selections.')
        existing = next((d for d in case['documents'] if d['request_id'] == str(request_id)), None)
        if existing:
            return public_case(case)
        require_revision(case, expected_revision)
        previous = doc_by_id(case, previous_document_id) if previous_document_id else None
        checked = []
        total_bytes = 0
        for file in files:
            raw = await file.read(settings.max_upload_mb * 1024 * 1024 + 1)
            total_bytes += len(raw)
            if total_bytes > settings.max_upload_batch_mb * 1024 * 1024:
                raise HTTPException(413, f'Upload at most {settings.max_upload_batch_mb} MB of files in one batch.')
            metadata = await run_in_threadpool(inspect_file, raw, file.filename or 'document', settings)
            checked.append((raw, metadata))
        prepared, pages = await run_in_threadpool(prepare_document, checked, manifest, settings)
        signature = hashlib.sha256(prepared).hexdigest()
        # Compare originals + page selection, since PDF serializer metadata may vary.
        identity = hashlib.sha256(json.dumps([[(m['sha256']) for _, m in checked], json.loads(manifest)], sort_keys=True).encode()).hexdigest()
        duplicate = next((d for d in case['documents'] if d['identity'] == identity), None)
        if duplicate:
            raise HTTPException(409, 'These same pages are already in this case. Open the existing document to retry processing.')
        await paid(auth, 'pages', len(pages))
        document_id = uid()
        prefix = f'{auth[0]}/{case["id"]}/{document_id}'
        originals = []
        for _, metadata in checked:
            extension = {'application/pdf': 'pdf', 'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp'}[metadata['mime']]
            metadata['path'] = f'{prefix}/{metadata["id"]}.{extension}'
            originals.append(metadata)
        document = {'id': document_id, 'request_id': str(request_id), 'filename': originals[0]['filename'] if len(originals) == 1 else f'{len(pages)} scanned pages',
                    'version': previous['version'] + 1 if previous else 1, 'previous_id': previous['id'] if previous else None,
                    'status': 'uploading', 'complete': complete, 'pages': pages, 'originals': originals,
                    'sha256': signature, 'identity': identity, 'processed_path': f'{prefix}/processed.pdf', 'created_at': now(),
                    'provider_job_id': None, 'error': None}
        case['documents'].append(document)
        await save(case, auth)  # Persist all planned paths before writing, so deletion can clean partial uploads.
        try:
            for raw, metadata in checked:
                await repo.put_file(metadata['path'], raw, metadata['mime'], auth[1])
            await repo.put_file(document['processed_path'], prepared, 'application/pdf', auth[1])
            document['status'] = 'starting'
            await save(case, auth)
            await paid(auth)
            document['provider_job_id'] = await ai.start_document(prepared, case['language'])
            document['status'] = 'extracting'
            await save(case, auth)
        except HTTPException as error:
            document['status'] = 'failed'
            document['error'] = error.detail
            await save(case, auth)
        return public_case(case)

    @app.get('/api/cases/{case_id}/documents/{document_id}/status')
    async def document_status(case_id: UUID, document_id: UUID, auth: Session):
        case = await get_case(case_id, auth)
        document = doc_by_id(case, document_id)
        if document['status'] not in ('extracting', 'starting'):
            return public_case(case)
        if not document['provider_job_id']:
            document['status'] = 'failed'
            document['error'] = 'Submission was interrupted before its provider ID was saved. Check provider usage before explicitly retrying.'
            await save(case, auth)
            return public_case(case)
        status = await ai.document_status(document['provider_job_id'])
        state = status['status']
        if state in ('failed', 'rejected'):
            document['status'] = 'failed'
            document['error'] = 'Sarvam could not extract this document. Check the pages and retry.'
        elif state in ('completed', 'partially_completed'):
            result = await ai.document_results(document['provider_job_id'])
            texts = extracted_pages(result, len(document['pages']))
            for page in document['pages']:
                page['machine_text'] = texts.get(page['number'], '')
                if not page['machine_text'].strip():
                    page['issues'].append('No readable text returned for this page. Replace the image or enter a checked transcription.')
            document['partial'] = state == 'partially_completed' or len(texts) != len(document['pages']) or any(not t.strip() for t in texts.values())
            document['status'] = 'awaiting_confirmation'
        else:
            elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(document['created_at'])).total_seconds()
            if elapsed > settings.document_job_timeout_seconds:
                document['error'] = 'Extraction is taking longer than expected. The provider job is preserved; check again to resume.'
            return public_case(case)
        await save(case, auth)
        return public_case(case)

    @app.post('/api/cases/{case_id}/documents/{document_id}/retry')
    async def retry_document(case_id: UUID, document_id: UUID, auth: Session):
        case = await get_case(case_id, auth)
        document = doc_by_id(case, document_id)
        if document['status'] not in ('failed', 'uploading'):
            raise HTTPException(409, 'Use Check progress for an existing extraction job.')
        if document['provider_job_id']:
            state = await ai.document_status(document['provider_job_id'])
            if state['status'] not in ('failed', 'rejected'):
                document['status'] = 'extracting'
                document['error'] = None
                await save(case, auth)
                return public_case(case)
        prepared = await repo.get_file(document['processed_path'], auth[1])
        document['status'] = 'starting'
        await save(case, auth)
        await paid(auth, 'pages', len(document['pages']))
        document['provider_job_id'] = await ai.start_document(prepared, case['language'])
        document['status'] = 'extracting'
        document['error'] = None
        await save(case, auth)
        return public_case(case)

    @app.post('/api/cases/{case_id}/documents/{document_id}/confirm')
    async def review_document(case_id: UUID, document_id: UUID, body: ReviewInput, auth: Session):
        case = await get_case(case_id, auth)
        document = doc_by_id(case, document_id)
        if document['status'] not in ('awaiting_confirmation', 'ready'):
            raise HTTPException(409, 'Wait for extraction or retry the failed document first.')
        valid_ids = {p['id'] for p in document['pages']}
        if set(body.pages) != valid_ids or any(len(t) > 40000 for t in body.pages.values()):
            raise HTTPException(422, 'Review every supplied page. Page text is limited to 40,000 characters.')
        if not await begin(case, body, auth, 'document_confirmation'):
            return public_case(case)
        checkpoint(case, 'User checked document text and completeness')
        for page in document['pages']:
            text = body.pages[page['id']]
            if page['confirmed_text'] != text:
                page['corrections'].append({'before': page['confirmed_text'], 'after': text, 'at': now()})
            page['confirmed_text'] = text
        document['complete'] = body.complete
        document['partial'] = any(not p['confirmed_text'].strip() for p in document['pages'])
        document['status'] = 'ready'
        document['confirmed_at'] = now()
        finish(case, body)
        await save(case, auth)
        return public_case(case)

    @app.get('/api/cases/{case_id}/documents/{document_id}/originals/{original_id}')
    async def original_file(case_id: UUID, document_id: UUID, original_id: UUID, auth: Session):
        case = await get_case(case_id, auth)
        document = doc_by_id(case, document_id)
        original = next((o for o in document['originals'] if o['id'] == str(original_id)), None)
        if not original:
            raise HTTPException(404, 'Original not found in this document.')
        return Response(await repo.get_file(original['path'], auth[1]), media_type=original['mime'])

    @app.get('/api/cases/{case_id}/evidence/{segment_id}')
    async def evidence(case_id: UUID, segment_id: UUID, auth: Session):
        case = await get_case(case_id, auth)
        source = source_map(case).get(str(segment_id))
        if not source:
            raise HTTPException(404, 'Passage not found in this case.')
        return source

    @app.post('/api/cases/{case_id}/analysis')
    async def analyse(case_id: UUID, body: AnalysisInput, auth: Session):
        case = await get_case(case_id, auth)
        role = next((f for f in case['facts'] if f['key'] == 'role' and f['status'] == 'user_confirmed' and f['value'].strip()), None)
        if not role:
            raise HTTPException(422, 'Confirm your role before reviewing favourable or risky terms.')
        if body.document_id:
            document = doc_by_id(case, body.document_id)
            if document['status'] != 'ready':
                raise HTTPException(422, 'Review the extracted document text first.')
        ctx, sources = context(case, document_id=str(body.document_id) if body.document_id else None)
        if not any(s['kind'] == 'document' for s in sources.values()):
            raise HTTPException(422, 'Upload and confirm readable document text before requesting a document review.')
        if not await begin(case, body, auth, 'analysis'):
            return public_case(case)
        await paid(auth)
        assessment, metrics = await ai.complete('Review the supplied documents from the confirmed user role and goal. Give a plain summary, favourable terms, potential risks, unclear terms and next steps. Each finding needs a supporting exact passage; empty lists are honest. Explain why attention matters and what to ask. Treat unreadable/missing pages as limits. Do not assess enforceability.', ctx, Assessment, reasoning=True)
        result = validate_assessment(assessment.model_dump(), sources)
        if not await ai.check_support(json.dumps(result, ensure_ascii=False), ctx['evidence'] + [{'facts': case['facts']}]):
            raise HTTPException(502, 'The proposed review failed the evidence check. No review was published. Try reviewing one document.')
        if any(not d['complete'] or d.get('partial') for d in case['documents'] if not body.document_id or d['id'] == str(body.document_id)):
            result['gaps'].insert(0, 'Partial review: completeness or readability is not established. Missing terms mean not found in the readable supplied pages.')
        result.update({'id': uid(), 'document_id': str(body.document_id) if body.document_id else None, 'revision': case['revision'], 'created_at': now(), 'metrics': metrics})
        result['output_id'] = output(case, result['summary'], 'summary')
        case['assessments'].append(result)
        finish(case, body)
        await save(case, auth)
        return public_case(case)

    @app.post('/api/cases/{case_id}/updates')
    async def compare(case_id: UUID, body: CompareInput, auth: Session):
        case = await get_case(case_id, auth)
        before, after = doc_by_id(case, body.before_document_id), doc_by_id(case, body.after_document_id)
        if before['id'] == after['id'] or before['status'] != 'ready' or after['status'] != 'ready':
            raise HTTPException(422, 'Choose two different documents with confirmed extraction.')
        if not await begin(case, body, auth, 'comparison'):
            return public_case(case)
        comparison = compare_documents(before, after)
        comparison['affected_assessments'] = [a['id'] for a in case['assessments'] if a['document_id'] in (None, before['id'])]
        case['comparisons'].append(comparison)
        finish(case, body)
        await save(case, auth)
        return public_case(case)

    @app.post('/api/cases/{case_id}/drafts')
    async def draft(case_id: UUID, body: DraftInput, auth: Session):
        case = await get_case(case_id, auth)
        if not await begin(case, body, auth, 'draft'):
            return public_case(case)
        await paid(auth)
        ctx, _ = context(case)
        result, metrics = await ai.complete(f'Prepare a neutral {body.kind} communication in the selected language. Use confirmed facts only, conditional arithmetic and explicit [placeholders]. Do not invent accusations, legal threats or citations. This draft is for the user to edit and send.', ctx, DraftOutput, reasoning=True)
        item = {'id': uid(), 'kind': body.kind, 'revision': case['revision'], 'version': 1, 'language': case['language'],
                'text': result.text, 'placeholders': result.placeholders, 'history': [], 'created_at': now(), 'metrics': metrics}
        case['drafts'].append(item)
        finish(case, body)
        await save(case, auth)
        return public_case(case)

    @app.patch('/api/cases/{case_id}/drafts/{draft_id}')
    async def edit_draft(case_id: UUID, draft_id: UUID, body: DraftEdit, auth: Session):
        case = await get_case(case_id, auth)
        item = next((d for d in case['drafts'] if d['id'] == str(draft_id)), None)
        if not item:
            raise HTTPException(404, 'Draft not found.')
        if not await begin(case, body, auth, 'draft_edit'):
            return public_case(case)
        item['history'].append({'version': item['version'], 'text': item['text'], 'at': now()})
        item['version'] += 1
        item['text'] = body.text
        item['edited_at'] = now()
        finish(case, body)
        await save(case, auth)
        return public_case(case)

    @app.post('/api/cases/{case_id}/transcriptions')
    async def transcribe(case_id: UUID, auth: Session, file: Annotated[UploadFile, File()]):
        case = await get_case(case_id, auth)
        mime = (file.content_type or '').split(';')[0]
        if mime not in ('audio/wav', 'audio/webm', 'audio/mp4', 'audio/ogg'):
            raise HTTPException(415, 'Use WAV, WebM, MP4 or OGG audio; typed input is always available.')
        data = await file.read(4 * 1024 * 1024 + 1)
        duration = await run_in_threadpool(audio_duration, data, mime, settings.max_audio_seconds)
        await paid(auth, 'audio', math.ceil(duration))
        await paid(auth)
        transcript = await ai.transcribe(data, mime, case['language'])
        item = {'id': uid(), 'original_text': transcript, 'at': now(), 'language': case['language'], 'audio_retained': False}
        case['transcripts'].append(item)
        await save(case, auth)
        return item

    def find_output(case, output_id):
        item = next((o for o in case['outputs'] if o['id'] == str(output_id)), None)
        if not item:
            raise HTTPException(404, 'Explanation not found in this case.')
        return item

    @app.post('/api/cases/{case_id}/speech')
    async def speech(case_id: UUID, body: OutputInput, auth: Session):
        case = await get_case(case_id, auth)
        item = find_output(case, body.output_id)
        if item['language'] != body.language:
            raise HTTPException(422, 'Translate this explanation first so the spoken and displayed text match.')
        await paid(auth)
        audio = await ai.speak(item['text'], body.language)
        if len(audio) > settings.max_request_bytes:
            raise HTTPException(422, 'This audio exceeds the deployment limit. Ask for a shorter summary and play that instead.')
        return Response(audio, media_type='audio/wav')

    @app.post('/api/cases/{case_id}/translation')
    async def translation(case_id: UUID, body: OutputInput, auth: Session):
        case = await get_case(case_id, auth)
        item = find_output(case, body.output_id)
        await paid(auth)
        translated = await ai.translate(item['text'], item['language'], body.language)
        # Second check evaluates names and qualifications as well as deterministic numeral matching.
        if not await ai.check_support(translated, [{'original_explanation': item['text'], 'task': 'Translation must preserve meaning, names, dates, negation and qualifications.'}]):
            raise HTTPException(502, 'Translation did not pass the meaning check. The original explanation remains available.')
        result = {**item, 'id': uid(), 'text': translated, 'language': body.language, 'original_output_id': item['id'], 'created_at': now()}
        case['outputs'].append(result)
        await save(case, auth)
        return result

    @app.get('/api/cases/{case_id}/export')
    async def export(case_id: UUID, auth: Session):
        case = await get_case(case_id, auth)
        return JSONResponse(public_case(case), headers={'Content-Disposition': f'attachment; filename="eagle-eye-{case_id}.json"'})

    @app.post('/api/cases/{case_id}/brief')
    async def brief(case_id: UUID, auth: Session):
        case = await get_case(case_id, auth)
        facts = '\n'.join(f'- {f["label"]}: {f["value"] or "Unknown"} ({f["status"]})' for f in case['facts'])
        events = '\n'.join(f'- {m["at"][:10]} — {m["text"]}' for m in case['messages'] if m['role'] == 'user')
        documents = '\n'.join(f'- {d["filename"]}, version {d["version"]}, {len(d["pages"])} pages, {d["status"]}; complete: {d["complete"]}' for d in case['documents'])
        sources = '\n'.join(f'- {s["id"]}: page {s["page"]}, version {s["version"]}\n  {s["text"]}' for s in source_map(case).values() if s['kind'] == 'document')
        gaps = '\n'.join(f'- {gap}' for a in case['assessments'][-1:] for gap in a['gaps'])
        text = f'EAGLE EYE · LAWYER BRIEF\n{case["title"]}\nRevision {case["revision"]} · {now()}\n\nCONFIRMED / DISPUTED STATEMENTS\n{facts or "None confirmed"}\n\nUSER-REPORTED DEVELOPMENTS (not independently verified)\n{events}\n\nEVIDENCE INDEX\n{documents}\n\nCONDITIONAL RECONCILIATION (integer paise; 100 paise = INR 1)\n{json.dumps(reconciliation(case["ledger"]), ensure_ascii=False, indent=2)}\n\nDOCUMENT PASSAGES\n{sources}\n\nGAPS AND REVIEW QUESTIONS\n{gaps}\n- Which law and procedure apply to this worker, location and event date?\n- Are the stated conditions enforceable in these circumstances?\n\nSOURCE COVERAGE\n{COVERAGE}\nOfficial help navigation: https://nalsa.gov.in/legal-aid/\n\nPrepared by the user with AI assistance. No professional review or lawyer engagement has occurred.'
        return {'text': text, 'revision': case['revision'], 'created_at': now()}

    @app.delete('/api/cases/{case_id}')
    async def delete_case(case_id: UUID, auth: Session):
        case = await get_case(case_id, auth, True)
        if case.get('deletion_status') == 'complete':
            return {'status': 'complete'}
        case['deletion_status'] = 'pending'
        await save(case, auth, True)  # Tombstone first: late completions can no longer save.
        paths = [p for d in case['documents'] for p in [d.get('processed_path'), *[o['path'] for o in d['originals']]]]
        try:
            await repo.delete_files([p for p in paths if p], auth[1])
        except HTTPException:
            return JSONResponse({'status': 'pending', 'message': 'Access revoked. File cleanup is pending; retry Delete to finish.'}, status_code=202)
        minimal = {'id': case['id'], 'storage_version': case['storage_version'], 'deletion_status': 'complete', 'documents': [], 'updated_at': now()}
        await save(minimal, auth, True)
        return {'status': 'complete', 'message': 'Application records and files removed. Processor and backup retention is governed by those services.'}

    return app


app = create_app()
