import { useEffect, useState } from 'react';
import { api } from '../api/rest';
import { useStore } from '../store/useStore';

const VISIBLE_MS = 45_000; // fades out after this long unless the student is interacting

const humanize = (id: string) => id.replace(/_/g, ' ');

// Speech bubble: tutor question + hint level + reply box + mic.
// Never grabs focus on its own (the student is typing in VS Code); only clicks focus the input.
export function Bubble() {
  const bubble = useStore((s) => s.bubble);
  const bubbleOpen = useStore((s) => s.bubbleOpen);
  const closeBubble = useStore((s) => s.closeBubble);
  const openBubble = useStore((s) => s.openBubble);
  const hud = useStore((s) => s.mode === 'system_design');
  const at = bubble?.at;

  const [visible, setVisible] = useState(false);
  const [draft, setDraft] = useState('');
  const [focused, setFocused] = useState(false);
  const [hover, setHover] = useState(false);
  const [listening, setListening] = useState(false);
  const [status, setStatus] = useState<'idle' | 'sending' | 'error'>('idle');

  useEffect(() => {
    if (at) {
      setVisible(true);
      setStatus('idle');
      openBubble();
    }
  }, [at, openBubble]);

  useEffect(() => {
    if (!visible || !bubbleOpen || focused || hover || draft || listening) return;
    const t = setTimeout(() => {
      setVisible(false);
      closeBubble();
    }, VISIBLE_MS);
    return () => clearTimeout(t);
  }, [visible, bubbleOpen, focused, hover, draft, listening, at, closeBubble]);

  if (!bubble || (!visible && !bubbleOpen)) return null;
  const q = bubble.data;

  const send = async () => {
    const text = draft.trim();
    if (!text || status === 'sending') return;
    setStatus('sending');
    try {
      await api.reply(text);
      setDraft('');
    } catch {
      setStatus('error');
    } finally {
      setStatus('idle');
    }
  };

  const toggleMic = async () => {
    try {
      if (!listening) { await api.voiceStart(); setListening(true); }
      else { await api.voiceStop(); setListening(false); }
    } catch { setListening(false); setStatus('error'); }
  };

  return (
    <div
      data-hit
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      onKeyDown={(e) => {
        if (e.key === 'Escape') {
          setVisible(false);
          closeBubble();
        }
      }}
      className={`bubble-in relative w-[330px] rounded-2xl border bg-[rgba(14,16,28,0.82)] p-3 backdrop-blur-md ${hud ? 'border-[#33ff9c]/50 shadow-[0_0_24px_rgba(51,255,156,0.25)]' : 'border-white/15 shadow-glow'}`}
    >
      <div className="mb-1.5 flex items-center justify-between text-[10px] text-slate-400">
        <div className="flex items-center gap-2">
          <span className="flex gap-0.5" title={`Hint level ${q.hint_level} of 4`}>
            {[0, 1, 2, 3, 4].map((i) => (
              <span key={i} className={`h-1.5 w-3 rounded-full ${i <= q.hint_level ? 'bg-accent' : 'bg-white/10'}`} />
            ))}
          </span>
          <span className="rounded-full bg-white/10 px-2 py-0.5 text-slate-300">{humanize(q.targets_concept)}</span>
        </div>
        <button
          onClick={() => {
            setVisible(false);
            closeBubble();
          }}
          className="px-1 text-slate-400 hover:text-white"
          aria-label="Dismiss"
        >✕</button>
      </div>

      <p className="text-sm leading-snug text-slate-100">{q.question}</p>

      <div className="mt-2.5 flex items-center gap-1.5">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          onKeyDown={(e) => { if (e.key === 'Enter') void send(); }}
          placeholder={listening ? 'Listening…' : 'Your answer…'}
          disabled={listening}
          className="min-w-0 flex-1 rounded-lg border border-white/10 bg-black/30 px-2.5 py-1.5 text-xs text-slate-100 outline-none placeholder:text-slate-500 focus:border-accent/60"
        />
        <button
          onClick={() => void toggleMic()}
          title={listening ? 'Stop listening' : 'Speak your answer'}
          className={`h-7 w-7 shrink-0 rounded-lg border text-xs ${listening ? 'mic-live border-warn bg-warn/20 text-warn' : 'border-white/10 bg-white/5 text-slate-300 hover:border-accent/60'}`}
        >🎤</button>
        <button
          onClick={() => void send()}
          disabled={!draft.trim() || status === 'sending'}
          className="h-7 shrink-0 rounded-lg bg-accent/80 px-2.5 text-xs font-medium text-[#0b0d17] disabled:opacity-30"
        >{status === 'sending' ? '…' : 'Send'}</button>
      </div>
      {status === 'error' && <p className="mt-1 text-[10px] text-warn">Couldn’t reach the daemon. Is it running?</p>}

      <span className="absolute -bottom-1.5 left-1/2 h-3 w-3 -translate-x-1/2 rotate-45 border-b border-r border-white/15 bg-[rgba(14,16,28,0.82)]" />
    </div>
  );
}
