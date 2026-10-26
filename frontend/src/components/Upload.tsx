import { useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, Camera, FileUp, LoaderCircle, RotateCw, Trash2 } from 'lucide-react';
import * as pdfjs from 'pdfjs-dist';
import worker from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import { api, capabilities } from '../lib/api';
import { tr } from '../lib/i18n';
import type { CaseRecord, Language } from '../lib/types';

pdfjs.GlobalWorkerOptions.workerSrc = worker;
interface Preview { file_index: number; page: number; rotation: number; preview: string; name: string }
export function Upload({caseData, language, done, fail}: {caseData: CaseRecord; language: Language; done: (data: CaseRecord) => void; fail: (error: string) => void}) {
  const [files, setFiles] = useState<File[]>([]), [pages, setPages] = useState<Preview[]>([]), [busy, setBusy] = useState(false);
  const [complete, setComplete] = useState(false), [previous, setPrevious] = useState('');
  const picker = useRef<HTMLInputElement>(null), camera = useRef<HTMLInputElement>(null);
  const add = async (selected: FileList | null) => {
    if (!selected?.length) return;
    setBusy(true);
    try {
      const limits = await capabilities();
      const newFiles = [...files], newPages = [...pages];
      for (const file of Array.from(selected)) {
        if (file.size > limits.max_upload_mb * 1024 * 1024 || !['image/jpeg', 'image/png', 'image/webp', 'application/pdf'].includes(file.type)) throw new Error(`Use JPEG, PNG, WebP or PDF, up to ${limits.max_upload_mb} MB per file.`);
        if (newFiles.reduce((size, item) => size + item.size, file.size) > limits.max_upload_batch_mb * 1024 * 1024) throw new Error(`Upload at most ${limits.max_upload_batch_mb} MB of files in one batch.`);
        if (newFiles.length >= limits.max_document_pages) throw new Error(`Choose no more than ${limits.max_document_pages} files.`);
        const index = newFiles.length; newFiles.push(file);
        if (file.type === 'application/pdf') {
          const loadingTask = pdfjs.getDocument({data: await file.arrayBuffer()});
          const pdf = await loadingTask.promise;
          try {
            if (pdf.numPages > limits.max_document_pages || newPages.length + pdf.numPages > limits.max_document_pages) throw new Error(`Choose no more than ${limits.max_document_pages} pages in total.`);
            for (let i = 1; i <= pdf.numPages; i++) {
              const page = await pdf.getPage(i), viewport = page.getViewport({scale: 0.35});
              const canvas = document.createElement('canvas'); canvas.width = viewport.width; canvas.height = viewport.height;
              await page.render({canvas, viewport}).promise;
              newPages.push({file_index: index, page: i, rotation: 0, preview: canvas.toDataURL('image/jpeg', 0.7), name: file.name});
            }
          } finally {await loadingTask.destroy();}
        } else {
          const preview = await new Promise<string>((resolve, reject) => {const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = reject; reader.readAsDataURL(file);});
          newPages.push({file_index: index, page: 1, rotation: 0, preview, name: file.name});
        }
        if (newPages.length > limits.max_document_pages) throw new Error(`Choose no more than ${limits.max_document_pages} pages in total.`);
      }
      setFiles(newFiles); setPages(newPages);
    } catch (e) {fail(e instanceof Error ? e.message : 'Could not preview this file. Check that the PDF is unlocked.');}
    finally {setBusy(false); if (picker.current) picker.current.value = ''; if (camera.current) camera.current.value = '';}
  };
  const move = (index: number, delta: number) => setPages(items => {const next = [...items]; [next[index], next[index + delta]] = [next[index + delta], next[index]]; return next;});
  const submit = async () => {
    setBusy(true);
    try {
      const form = new FormData(); files.forEach(file => form.append('files', file));
      form.append('manifest', JSON.stringify(pages.map(({file_index, page, rotation}) => ({file_index, page, rotation}))));
      form.append('request_id', crypto.randomUUID()); form.append('expected_revision', String(caseData.revision)); form.append('complete', String(complete));
      if (previous) form.append('previous_document_id', previous);
      done(await api<CaseRecord>(`/api/cases/${caseData.id}/documents`, {method: 'POST', body: form}));
    } catch (e) {fail(e instanceof Error ? e.message : 'Upload failed.');}
    finally {setBusy(false);}
  };
  return <div className="upload-flow"><p className="muted">{tr(language, 'processingNote')}</p><div className="upload-drop"><FileUp size={30}/><h3>{language === 'hi-IN' ? 'हर पृष्ठ को स्पष्ट रखें' : 'A clearer picture starts here'}</h3><p>JPEG, PNG, WebP, PDF · 3 MB / batch · 10 pages</p><div className="button-row"><button onClick={() => picker.current?.click()} disabled={busy}><FileUp size={17}/>{tr(language, 'upload')}</button><button className="secondary" onClick={() => camera.current?.click()} disabled={busy}><Camera size={17}/>{tr(language, 'scan')}</button></div><input hidden ref={picker} aria-label="Choose documents" type="file" multiple accept="image/jpeg,image/png,image/webp,application/pdf" onChange={e => void add(e.target.files)}/><input hidden ref={camera} aria-label="Photograph pages" type="file" accept="image/*" capture="environment" onChange={e => void add(e.target.files)}/></div>
    <div className="page-previews">{pages.map((page, i) => <div className="page-preview" key={`${page.file_index}-${page.page}`}><div className="thumbnail"><img style={{transform: `rotate(${page.rotation}deg)`}} src={page.preview} alt={`${page.name}, page ${page.page}`}/></div><b>{i + 1}. {page.name}</b><div className="button-row"><button className="icon-button" aria-label={`Move page ${i + 1} earlier`} disabled={!i || busy} onClick={() => move(i, -1)}><ArrowLeft size={16}/></button><button className="icon-button" aria-label={`Move page ${i + 1} later`} disabled={i === pages.length - 1 || busy} onClick={() => move(i, 1)}><ArrowRight size={16}/></button><button className="icon-button" aria-label={`Rotate page ${i + 1}`} disabled={busy} onClick={() => setPages(items => items.map((p, j) => j === i ? {...p, rotation: (p.rotation + 90) % 360} : p))}><RotateCw size={16}/></button><button className="icon-button" aria-label={`Remove page ${i + 1}`} disabled={busy} onClick={() => setPages(items => items.filter((_, j) => j !== i))}><Trash2 size={16}/></button></div></div>)}</div>
    {pages.length > 0 && <><label className="check"><input type="checkbox" checked={complete} onChange={e => setComplete(e.target.checked)}/>{tr(language, 'completeness')}</label>{caseData.documents.length > 0 && <label>Amendment of<select value={previous} onChange={e => setPrevious(e.target.value)}><option value="">New document</option>{caseData.documents.map(d => <option key={d.id} value={d.id}>{d.filename} · v{d.version}</option>)}</select></label>}<p className="small muted">Inspect blur, glare, cropped edges and orientation. Originals are preserved separately from the selected page order.</p><button disabled={busy} onClick={() => void submit()}>{busy ? <LoaderCircle className="spin" size={18}/> : <FileUp size={18}/>} {busy ? tr(language, 'sending') : `${tr(language, 'upload')} · ${pages.length}`}</button></>}
    {busy && <p role="status">{tr(language, 'sending')}</p>}</div>;
}
