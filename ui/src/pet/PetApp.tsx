// Pet window: art states, overlays, confetti, evolution, speech bubble.
import { useEffect, useRef, useState } from 'react';
import { useStore } from '../store/useStore';
import { openDashboard } from '../lib/tauri';
import { preloadArt } from './art/useArt';
import { Bubble } from './Bubble';
import { Confetti } from './Confetti';
import { EvolutionBar } from './EvolutionBar';
import { PetSprite } from './PetSprite';
import { useHitRects } from './useHitRects';
import { usePetPointer } from './usePetPointer';

export function PetApp() {
  const petState = useStore((s) => s.petState);
  const mode = useStore((s) => s.mode);
  const evo = useStore((s) => s.evolution);
  const repeat = useStore((s) => s.mistakeRepeat.count);
  const connected = useStore((s) => s.connected);

  const variant = evo?.variant ?? 'blob';
  const stage = evo?.stage ?? 1;

  const toggleBubble = useStore((s) => s.toggleBubble);

  useHitRects();
  const pointer = usePetPointer(openDashboard, toggleBubble);

  useEffect(() => { preloadArt(variant, stage); }, [variant, stage]);

  // stage-up flash
  const prevStage = useRef(stage);
  const [evolving, setEvolving] = useState(false);
  useEffect(() => {
    if (stage > prevStage.current) {
      setEvolving(true);
      const t = setTimeout(() => setEvolving(false), 1600);
      prevStage.current = stage;
      return () => clearTimeout(t);
    }
    prevStage.current = stage;
  }, [stage]);

  return (
    <div className="relative h-full">
      <Confetti />
      <div className="absolute inset-0 flex select-none flex-col items-center justify-end gap-2 pb-3">
        <Bubble />
        <div data-hit {...pointer} className="flex cursor-grab flex-col items-center gap-1 active:cursor-grabbing">
          <PetSprite state={petState} variant={variant} stage={stage} repeat={repeat} hardHat={mode === 'system_design'} evolving={evolving} />
          {evo && <EvolutionBar evo={evo} />}
        </div>
        {!connected && <span className="text-[10px] text-warn">offline · daemon not reachable</span>}
      </div>
    </div>
  );
}
