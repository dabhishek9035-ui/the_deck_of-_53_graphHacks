import { useEffect, useRef } from 'react';
import { startDragging } from '../lib/tauri';

const DRAG_THRESHOLD = 4; // px of movement before a press becomes a drag

// Press + move > threshold = drag the window. Press + release = click / double-click.
export function usePetPointer(onClick: () => void, onDoubleClick?: () => void) {
  const down = useRef<{ x: number; y: number } | null>(null);
  const clickRef = useRef(onClick);
  clickRef.current = onClick;
  const dblClickRef = useRef(onDoubleClick);
  dblClickRef.current = onDoubleClick;
  const lastClickTime = useRef<number>(0);
  const clickTimeout = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    const move = (e: MouseEvent) => {
      const d = down.current;
      if (!d) return;
      if (Math.hypot(e.clientX - d.x, e.clientY - d.y) > DRAG_THRESHOLD) {
        down.current = null;
        if (clickTimeout.current) {
          clearTimeout(clickTimeout.current);
          clickTimeout.current = null;
        }
        void startDragging();
      }
    };
    const up = () => {
      if (!down.current) return;
      down.current = null;
      const now = Date.now();
      if (now - lastClickTime.current < 320) {
        // Double tap!
        lastClickTime.current = 0;
        if (clickTimeout.current) {
          clearTimeout(clickTimeout.current);
          clickTimeout.current = null;
        }
        if (dblClickRef.current) {
          dblClickRef.current();
        } else {
          clickRef.current();
        }
      } else {
        lastClickTime.current = now;
        if (clickTimeout.current) clearTimeout(clickTimeout.current);
        clickTimeout.current = setTimeout(() => {
          clickRef.current();
          clickTimeout.current = null;
        }, 320);
      }
    };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
    return () => { window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up); };
  }, []);

  return {
    onMouseDown: (e: React.MouseEvent) => {
      if (e.button === 0) down.current = { x: e.clientX, y: e.clientY };
    },
  };
}
