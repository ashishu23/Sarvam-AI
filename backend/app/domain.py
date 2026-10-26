import copy
import difflib
import re

from fastapi import HTTPException

from .repository import now, uid

COVERAGE = 'Document explanation and factual case preparation. Substantive employment-law applicability has not been professionally reviewed; no signing verdict or legal entitlement determination.'


def new_case(data: dict) -> dict:
    return {
        'id': uid(), 'title': data['title'], 'domain': data['domain'], 'language': data['language'],
        'revision': 1, 'storage_version': 1, 'created_at': now(), 'updated_at': now(),
        'consent_at': now(), 'coverage': COVERAGE, 'facts': [], 'messages': [], 'documents': [],
        'assessments': [], 'drafts': [], 'proposals': [], 'revisions': [], 'transcripts': [],
        'comparisons': [], 'outputs': [], 'operations': {}, 'ledger': None, 'deletion_status': None,
    }


def require_revision(case: dict, revision: int):
    if case['revision'] != revision:
        raise HTTPException(409, 'Your view is out of date. Reload and review the latest confirmed revision.')


def checkpoint(case: dict, reason: str):
    case['revisions'].append({'revision': case['revision'], 'at': now(), 'reason': reason,
                             'facts': copy.deepcopy(case['facts']), 'ledger': copy.deepcopy(case['ledger']),
                             'document_ids': [d['id'] for d in case['documents']]})
    case['revision'] += 1


def reconciliation(ledger: dict | None) -> dict:
    if not ledger:
        return {'available': False, 'reason': 'Confirm salary periods, pay basis and payment attribution to calculate a balance.'}
    seen = set()
    payments = 0
    deductions = 0
    offers = []
    warnings = []
    for entry in ledger['entries']:
        key = entry['reference'].strip().casefold()
        if key in seen:
            warnings.append(f'Duplicate reference excluded: {entry["reference"]}')
            continue
        seen.add(key)
        if entry['kind'] == 'offer':
            offers.append(entry['amount_paise'])
        elif entry['confirmed'] and entry['attributed']:
            if entry['kind'] == 'payment':
                payments += entry['amount_paise']
            else:
                deductions += entry['amount_paise']
        else:
            warnings.append('An unconfirmed or unattributed entry is excluded from the balance.')
    comparable = ledger['comparable_confirmed'] and ledger['basis'] != 'unknown' and bool(ledger['period_labels'].strip())
    total = ledger['monthly_pay_paise'] * ledger['periods']
    balance = total - payments - deductions
    return {'available': comparable, 'stated_total_paise': total, 'payments_paise': payments,
            'deductions_paise': deductions, 'balance_paise': balance if comparable else None,
            'offers_paise': offers, 'difference_paise': balance - offers[-1] if offers and comparable else None,
            'warnings': warnings, 'reason': 'Conditional arithmetic from confirmed comparable inputs; this is not a finding of legal entitlement.' if comparable else 'Confirm comparable periods, basis and attribution first.'}


def source_map(case: dict, document_id: str | None = None) -> dict:
    sources = {}
    for doc in case['documents']:
        if document_id and doc['id'] != document_id:
            continue
        # Unreviewed OCR is never evidence for a definitive assessment.
        if doc['status'] != 'ready':
            continue
        for page in doc['pages']:
            text = page.get('confirmed_text', page.get('machine_text', ''))
            if text.strip():
                sources[page['id']] = {'id': page['id'], 'text': text, 'page': page['number'],
                    'document_id': doc['id'], 'version': doc['version'], 'filename': doc['filename'], 'kind': 'document'}
    for message in case['messages']:
        if message['role'] == 'user':
            sources[message['id']] = {'id': message['id'], 'text': message['text'], 'kind': 'user_statement'}
    return sources


def normalise(text: str) -> str:
    return re.sub(r'\s+', ' ', text).strip().casefold()


def validate_citations(citations: list[dict], sources: dict, documents_only: bool = False) -> list[dict]:
    valid = []
    for citation in citations:
        source = sources.get(citation['source_id'])
        if not source or (documents_only and source['kind'] != 'document'):
            continue
        if not normalise(citation['quote']) or normalise(citation['quote']) not in normalise(source['text']):
            continue
        valid.append({**citation, **{key: value for key, value in source.items() if key != 'text'},
                      'validation': 'Quote matched to source; interpretation requires review.'})
    return valid


def validate_assessment(assessment: dict, sources: dict) -> dict:
    for category in ('favourable', 'risks', 'unclear'):
        kept = []
        for finding in assessment[category]:
            refs = validate_citations(finding['citations'], sources, documents_only=True)
            if refs:
                finding['citations'] = refs
                kept.append(finding)
            else:
                assessment['gaps'].append('A proposed finding was withheld because its supporting passage could not be validated.')
        assessment[category] = kept
    return assessment


def compare_documents(before: dict, after: dict) -> dict:
    def lines(doc):
        return [f'Page {page["number"]}: {line}' for page in doc['pages'] for line in page.get('confirmed_text', '').splitlines() if line.strip()]
    changes = []
    a, b = lines(before), lines(after)
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag != 'equal':
            changes.append({'kind': tag, 'before': '\n'.join(a[i1:i2]), 'after': '\n'.join(b[j1:j2])})
    return {'id': uid(), 'before_id': before['id'], 'after_id': after['id'], 'changes': changes,
            'created_at': now(), 'limitation': 'Text comparison only. Reordered text can appear as a change. The effect on obligations needs a fresh, cited review.'}


def public_case(case: dict) -> dict:
    result = copy.deepcopy(case)
    result.pop('operations', None)
    result.pop('storage_version', None)
    for document in result['documents']:
        document.pop('processed_path', None)
        document.pop('provider_job_id', None)
        for original in document['originals']:
            original.pop('path', None)
    result['reconciliation'] = reconciliation(case['ledger'])
    return result
