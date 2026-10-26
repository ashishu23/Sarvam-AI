import { useEffect, useRef, type ReactNode } from 'react';
import { X } from 'lucide-react';

export function Modal({title, close, children, wide = false}: {title: string; close: () => void; children: ReactNode; wide?: boolean}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement;
    const element = ref.current;
    element?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close();
      if (event.key === 'Tab') {
        const items = element?.querySelectorAll<HTMLElement>('button:not(:disabled),a[href],input,select,textarea,[tabindex="0"]');
        if (!items?.length) return;
        const first = items[0], last = items[items.length - 1];
        if (event.shiftKey && (document.activeElement === first || document.activeElement === element)) { event.preventDefault(); last.focus(); }
        if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    };
    document.addEventListener('keydown', key);
    const overflow = document.body.style.overflow; document.body.style.overflow = 'hidden';
    return () => { document.removeEventListener('keydown', key); document.body.style.overflow = overflow; previous?.focus(); };
  }, [close]);
  return <div className="modal-backdrop" onMouseDown={e => {if (e.target === e.currentTarget) close();}}><div ref={ref} tabIndex={-1} role="dialog" aria-modal="true" aria-label={title} className={`modal ${wide ? 'wide' : ''}`}><div className="modal-head"><h2>{title}</h2><button className="icon-button" aria-label="Close" onClick={close}><X size={20}/></button></div>{children}</div></div>;
}
