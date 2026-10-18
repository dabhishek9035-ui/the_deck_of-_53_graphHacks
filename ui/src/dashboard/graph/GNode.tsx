import { Handle, Position, type Node, type NodeProps } from '@xyflow/react';
import { masteryTone } from '../util';

export type GNodeData = {
  kind: string;
  title: string;
  subtitle?: string;
  mastery?: number;
  weak: boolean;
  lit: boolean;
  delay: number; // ms, staggers the light-up along the path
  passed?: boolean;
  dimmed?: boolean;
  focused?: boolean;
  hovered?: boolean;
  onHover?: (id: string | null) => void;
  [key: string]: unknown;
};
export type GFlowNode = Node<GNodeData, 'gnode'>;

const KIND_COLOR: Record<string, string> = {
  Student: '#b388ff', Attempt: '#7fd4ff', Mistake: '#ff6b81', Concept: '#7c9cff',
};
const KIND_ICON: Record<string, string> = { Student: '◉', Attempt: '▶', Mistake: '✕', Concept: '⬡' };

export function GNode({ id, data, selected }: NodeProps<GFlowNode>) {
  const isPassed = data.passed === true;
  const isFailed = data.kind === 'Attempt' && data.passed === false;
  const color = isPassed ? '#4ade80' : isFailed ? '#ff6b81' : KIND_COLOR[data.kind] ?? '#8b93b0';

  return (
    <div
      className={`gnode ${data.lit ? 'gnode-lit' : ''} ${data.weak ? 'gnode-weak' : ''} ${selected || data.focused ? 'gnode-selected' : ''} ${data.dimmed ? 'gnode-dimmed' : ''} ${data.hovered ? 'gnode-hovered' : ''}`}
      style={{ ['--c' as string]: color, ['--delay' as string]: `${data.delay}ms` }}
      onMouseEnter={() => data.onHover?.(id)}
      onMouseLeave={() => data.onHover?.(null)}
    >
      {/* Left and Right handles for causality flow */}
      <Handle type="target" position={Position.Left} id="in" className="gh" />
      <Handle type="source" position={Position.Right} id="out" className="gh" />
      {/* Top and Bottom handles for prerequisite relationships */}
      <Handle type="source" position={Position.Bottom} id="ps" className="gh" />
      <Handle type="target" position={Position.Top} id="pt" className="gh" />

      <div className="flex items-start justify-between gap-1.5">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-xs shrink-0" style={{ color }}>{KIND_ICON[data.kind] ?? '●'}</span>
          <div className="min-w-0">
            <div className="truncate text-xs font-semibold" title={data.title}>{data.title}</div>
            {data.subtitle && <div className="truncate text-[10px] opacity-65" title={data.subtitle}>{data.subtitle}</div>}
          </div>
        </div>

        {/* Status Pill for Attempt */}
        {data.kind === 'Attempt' && data.passed !== undefined && (
          <span
            className="shrink-0 rounded px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wider"
            style={{
              background: isPassed ? 'rgba(74,222,128,0.18)' : 'rgba(255,107,129,0.18)',
              color: isPassed ? '#4ade80' : '#ff6b81',
              border: `1px solid ${isPassed ? 'rgba(74,222,128,0.3)' : 'rgba(255,107,129,0.3)'}`,
            }}
          >
            {isPassed ? 'Pass' : 'Fail'}
          </span>
        )}

        {/* Mastery Percentage for Concept */}
        {data.mastery !== undefined && (
          <span className="shrink-0 text-[10px] font-mono" style={{ color: masteryTone(data.mastery) }}>
            {Math.round(data.mastery * 100)}%
          </span>
        )}
      </div>

      {data.mastery !== undefined && (
        <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-white/10">
          <div className="h-full rounded-full transition-all duration-300" style={{ width: `${data.mastery * 100}%`, background: masteryTone(data.mastery) }} />
        </div>
      )}
    </div>
  );
}
