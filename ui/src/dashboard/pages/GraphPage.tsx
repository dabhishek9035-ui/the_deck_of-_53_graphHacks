import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Background,
  Controls,
  MiniMap,
  Panel,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type NodeChange,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { useStore } from '../../store/useStore';
import { DEMO_GRAPH, DEMO_PROGRESS } from '../graph/demoGraph';
import { GNode, type GFlowNode, type GNodeData } from '../graph/GNode';
import { NodeDetails } from '../graph/NodeDetails';
import { useGraphElements } from '../graph/useGraphElements';

const nodeTypes = { gnode: GNode };
const LIT_MS = 4200;
const ALL_KINDS = ['Student', 'Attempt', 'Mistake', 'Concept'] as const;

function Chip({
  on,
  onClick,
  children,
  badge,
  icon,
}: {
  on: boolean;
  onClick: () => void;
  children: React.ReactNode;
  badge?: number | string;
  icon?: string;
}) {
  return (
    <button
      onClick={onClick}
      className="flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium transition-all"
      style={{
        borderColor: on ? 'var(--d-accent)' : 'var(--d-border)',
        color: on ? '#ffffff' : 'var(--d-muted)',
        background: on ? 'color-mix(in srgb, var(--d-accent) 25%, rgba(14,16,28,0.85))' : 'rgba(14,16,28,0.7)',
        boxShadow: on ? '0 0 10px rgba(124,156,255,0.25)' : 'none',
      }}
    >
      {icon && <span className="opacity-80">{icon}</span>}
      <span>{children}</span>
      {badge !== undefined && (
        <span
          className="rounded-full px-1.5 py-0.2 text-[9px] font-mono"
          style={{
            background: on ? 'rgba(255,255,255,0.2)' : 'rgba(255,255,255,0.08)',
            color: on ? '#fff' : 'var(--d-muted)',
          }}
        >
          {badge}
        </span>
      )}
    </button>
  );
}

function Legend({
  activeKinds,
  onToggleKind,
  weakOnly,
  onToggleWeak,
}: {
  activeKinds: Set<string>;
  onToggleKind: (kind: string) => void;
  weakOnly: boolean;
  onToggleWeak: () => void;
}) {
  const items: [string, string, string][] = [
    ['Student', '#b388ff', '◉'],
    ['Attempt', '#7fd4ff', '▶'],
    ['Mistake', '#ff6b81', '✕'],
    ['Concept', '#7c9cff', '⬡'],
  ];

  return (
    <div
      className="glass-card absolute bottom-3 left-3 z-10 flex flex-wrap items-center gap-2 px-3 py-2 text-[11px]"
      style={{ background: 'rgba(14,16,28,0.85)', backdropFilter: 'blur(12px)' }}
    >
      <span className="text-[10px] uppercase tracking-wider font-semibold opacity-60 mr-1">Legend:</span>
      {items.map(([name, color, icon]) => {
        const on = activeKinds.has(name);
        return (
          <button
            key={name}
            onClick={() => onToggleKind(name)}
            className="flex items-center gap-1.5 rounded px-2 py-0.5 transition-opacity"
            style={{ opacity: on ? 1 : 0.45 }}
            title={`Toggle ${name} nodes`}
          >
            <span style={{ color }}>{icon}</span>
            <span style={{ color: on ? 'var(--d-text)' : 'var(--d-muted)' }}>{name}</span>
          </button>
        );
      })}
      <span className="h-3 w-px bg-white/10 mx-1" />
      <button
        onClick={onToggleWeak}
        className="flex items-center gap-1.5 rounded px-2 py-0.5 transition-all"
        style={{
          background: weakOnly ? 'rgba(255,107,129,0.2)' : 'transparent',
          color: '#ff6b81',
          border: weakOnly ? '1px solid rgba(255,107,129,0.4)' : '1px solid transparent',
        }}
        title="Toggle weak concept highlight filter"
      >
        <span className="h-2 w-2 rounded-full bg-[#ff6b81]" style={{ boxShadow: '0 0 6px #ff6b81' }} />
        <span>Weak concept</span>
      </button>
    </div>
  );
}

function GraphCanvas() {
  const highlight = useStore((s) => s.highlight);
  const graph = useStore((s) => s.graph);
  const { fitView, zoomIn, zoomOut, setViewport } = useReactFlow();

  // State
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [showPrereq, setShowPrereq] = useState(true);
  const [follow, setFollow] = useState(false);
  const [activeAt, setActiveAt] = useState(0);
  const [searchQuery, setSearchQuery] = useState('');
  const [weakOnly, setWeakOnly] = useState(false);
  const [trackFilter, setTrackFilter] = useState('all');
  const [activeKinds, setActiveKinds] = useState<Set<string>>(new Set(ALL_KINDS));
  const [showMiniMap, setShowMiniMap] = useState(true);
  const [scrollMode, setScrollMode] = useState<'zoom' | 'pan'>('zoom');
  const [draggable, setDraggable] = useState(true);
  const [customNodePositions, setCustomNodePositions] = useState<Map<string, { x: number; y: number }>>(new Map());

  // Light-up window from graph_update
  useEffect(() => {
    if (!highlight) return;
    const remaining = LIT_MS - (Date.now() - highlight.at);
    if (remaining <= 0) return;
    setActiveAt(highlight.at);
    const t = setTimeout(() => setActiveAt(0), remaining);
    return () => clearTimeout(t);
  }, [highlight]);

  const lit = useMemo(
    () => (highlight && activeAt === highlight.at ? { nodeIds: highlight.nodeIds, edgeKeys: highlight.edgeKeys } : null),
    [highlight, activeAt],
  );

  // Compute graph elements using barycenter layout and neighborhood focus
  const { nodes: rawNodes, edges, lanes } = useGraphElements({
    showPrereq,
    selectedId,
    hoveredId,
    lit,
    activeKinds,
    trackFilter,
    weakOnly,
    searchQuery,
    onNodeHover: setHoveredId,
  });

  // Apply any custom positions from user dragging
  const nodes = useMemo(() => {
    if (customNodePositions.size === 0) return rawNodes;
    return rawNodes.map((n) => {
      const custom = customNodePositions.get(n.id);
      return custom ? { ...n, position: custom, draggable } : { ...n, draggable };
    });
  }, [rawNodes, customNodePositions, draggable]);

  const onNodesChange = useCallback((changes: NodeChange<GFlowNode>[]) => {
    setCustomNodePositions((prev) => {
      const next = new Map(prev);
      for (const c of changes) {
        if (c.type === 'position' && c.position) {
          next.set(c.id, c.position);
        }
      }
      return next;
    });
  }, []);

  const handleTidy = useCallback(() => {
    setCustomNodePositions(new Map());
    setTimeout(() => {
      void fitView({ padding: 0.18, duration: 500 });
    }, 50);
  }, [fitView]);

  // Extract available tracks from concepts
  const availableTracks = useMemo(() => {
    const tracks = new Set<string>();
    for (const n of Object.values(graph.nodes)) {
      if (n.label === 'Concept' && n.props.track) {
        tracks.add(String(n.props.track));
      }
    }
    return Array.from(tracks);
  }, [graph.nodes]);

  // Follow live active path
  useEffect(() => {
    if (!follow || !lit || !lit.nodeIds.length) return;
    const t = setTimeout(
      () => void fitView({ nodes: lit.nodeIds.map((id) => ({ id })), padding: 0.5, maxZoom: 1.2, duration: 600 }),
      60,
    );
    return () => clearTimeout(t);
  }, [lit, follow, fitView]);

  const toggleKind = (kind: string) => {
    setActiveKinds((prev) => {
      const next = new Set(prev);
      if (next.has(kind)) {
        if (next.size > 1) next.delete(kind);
      } else {
        next.add(kind);
      }
      return next;
    });
  };

  return (
    <div className="relative h-full select-none">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        colorMode="dark"
        nodesDraggable={draggable}
        nodesConnectable={false}
        elementsSelectable={true}
        minZoom={0.05}
        maxZoom={3.5}
        zoomOnScroll={scrollMode === 'zoom'}
        panOnScroll={scrollMode === 'pan'}
        panOnDrag={true}
        zoomOnPinch={true}
        fitView
        fitViewOptions={{ padding: 0.18, duration: 400 }}
        onNodesChange={onNodesChange}
        onNodeClick={(_, n) => setSelectedId((prev) => (prev === n.id ? null : n.id))}
        onPaneClick={() => {
          setSelectedId(null);
          setHoveredId(null);
        }}
      >
        <Background gap={24} color="rgba(255,255,255,0.05)" />

        {/* Stage / Column Swimlane Headers Overlay */}
        <Panel position="top-left" className="pointer-events-none mt-14 ml-4">
          <div className="flex gap-4">
            {lanes.map((lane) => (
              <div
                key={lane.id}
                className="glass-card flex items-center justify-between px-3 py-1.5"
                style={{
                  width: lane.width,
                  background: 'rgba(14,16,28,0.7)',
                  borderColor: 'var(--d-border)',
                }}
              >
                <div className="flex items-center gap-2">
                  <span className="h-2 w-2 rounded-full" style={{ background: lane.color }} />
                  <span className="text-[11px] font-semibold uppercase tracking-wider" style={{ color: lane.color }}>
                    {lane.label}
                  </span>
                </div>
                <span className="rounded bg-white/10 px-1.5 py-0.2 font-mono text-[10px]" style={{ color: 'var(--d-muted)' }}>
                  {lane.count}
                </span>
              </div>
            ))}
          </div>
        </Panel>

        {/* Standard Flow Controls */}
        <Controls showInteractive={false} position="bottom-right" className="!mb-14 !mr-3" />

        {/* MiniMap */}
        {showMiniMap && (
          <MiniMap
            position="bottom-right"
            className="!m-3"
            zoomable
            pannable
            nodeColor={(n) => {
              const kind = (n.data as GNodeData)?.kind;
              if (kind === 'Student') return '#b388ff';
              if (kind === 'Attempt') return '#7fd4ff';
              if (kind === 'Mistake') return '#ff6b81';
              if (kind === 'Concept') return '#7c9cff';
              return '#8b93b0';
            }}
          />
        )}
      </ReactFlow>

      {/* Top Filter and Navigation Bar */}
      <div
        className="glass-card absolute left-3 top-3 right-3 z-10 flex flex-wrap items-center justify-between gap-3 p-2.5"
        style={{ background: 'rgba(14,16,28,0.85)', backdropFilter: 'blur(16px)' }}
      >
        {/* Left Section: Search and Kind Filters */}
        <div className="flex flex-wrap items-center gap-2 min-w-0">
          {/* Search Input */}
          <div className="relative">
            <input
              type="text"
              placeholder="Search concepts, mistakes..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-48 rounded-lg border px-2.5 py-1 text-xs text-white placeholder-white/40 focus:w-60 focus:outline-none transition-all"
              style={{
                borderColor: searchQuery ? 'var(--d-accent)' : 'var(--d-border)',
                background: 'rgba(255,255,255,0.06)',
              }}
            />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery('')}
                className="absolute right-2 top-1.5 text-xs text-white/50 hover:text-white"
              >
                ✕
              </button>
            )}
          </div>

          <div className="h-4 w-px bg-white/10" />

          {/* Kind Toggles */}
          {ALL_KINDS.map((kind) => {
            const count = Object.values(graph.nodes).filter((n) => n.label === kind).length;
            return (
              <Chip
                key={kind}
                on={activeKinds.has(kind)}
                onClick={() => toggleKind(kind)}
                badge={count}
              >
                {kind}
              </Chip>
            );
          })}

          {/* Track Selector */}
          {availableTracks.length > 0 && (
            <select
              value={trackFilter}
              onChange={(e) => setTrackFilter(e.target.value)}
              className="rounded-full border px-2.5 py-1 text-[11px] font-medium text-white focus:outline-none cursor-pointer"
              style={{
                background: trackFilter !== 'all' ? 'color-mix(in srgb, var(--d-accent) 25%, rgba(14,16,28,0.85))' : 'rgba(14,16,28,0.7)',
                borderColor: trackFilter !== 'all' ? 'var(--d-accent)' : 'var(--d-border)',
              }}
            >
              <option value="all">All Tracks</option>
              {availableTracks.map((t) => (
                <option key={t} value={t}>Track: {t.toUpperCase()}</option>
              ))}
            </select>
          )}

          <Chip on={weakOnly} onClick={() => setWeakOnly(!weakOnly)} icon="⚠">
            Weak only
          </Chip>
          <Chip on={showPrereq} onClick={() => setShowPrereq(!showPrereq)}>
            Prerequisites
          </Chip>
        </div>

        {/* Right Section: View & Navigation Actions */}
        <div className="flex items-center gap-1.5 shrink-0">
          <Chip on={follow} onClick={() => setFollow(!follow)} icon="●">
            Follow live
          </Chip>

          <div className="h-4 w-px bg-white/10" />

          <button
            onClick={() => void fitView({ padding: 0.18, duration: 450 })}
            className="rounded border border-white/10 bg-white/5 px-2.5 py-1 text-[11px] font-medium text-white/80 hover:bg-white/15 hover:text-white transition-colors"
            title="Fit graph to screen"
          >
            Fit View
          </button>
          <button
            onClick={() => void setViewport({ x: 100, y: 100, zoom: 1 }, { duration: 400 })}
            className="rounded border border-white/10 bg-white/5 px-2 py-1 text-[11px] font-medium text-white/80 hover:bg-white/15 hover:text-white transition-colors"
            title="Reset to 100% zoom"
          >
            100%
          </button>
          <button
            onClick={() => void zoomIn({ duration: 300 })}
            className="rounded border border-white/10 bg-white/5 px-2 py-1 text-[11px] font-medium text-white/80 hover:bg-white/15 hover:text-white transition-colors"
            title="Zoom In"
          >
            +
          </button>
          <button
            onClick={() => void zoomOut({ duration: 300 })}
            className="rounded border border-white/10 bg-white/5 px-2 py-1 text-[11px] font-medium text-white/80 hover:bg-white/15 hover:text-white transition-colors"
            title="Zoom Out"
          >
            -
          </button>

          <div className="h-4 w-px bg-white/10" />

          {/* Tidy Auto-Layout */}
          <button
            onClick={handleTidy}
            className="flex items-center gap-1 rounded border border-white/10 bg-white/5 px-2.5 py-1 text-[11px] font-medium text-amber-300 hover:bg-white/15 transition-colors"
            title="Re-run clean barycenter auto-layout and reset dragged nodes"
          >
            <span>⚡</span>
            <span>Tidy</span>
          </button>

          {/* Toggle Drag Mode */}
          <button
            onClick={() => setDraggable(!draggable)}
            className="rounded border border-white/10 bg-white/5 px-2 py-1 text-[11px] font-medium transition-colors"
            style={{ color: draggable ? 'var(--d-accent)' : 'var(--d-muted)' }}
            title={draggable ? 'Node dragging enabled (click to lock)' : 'Nodes locked (click to allow dragging)'}
          >
            {draggable ? '✋ Free' : '🔒 Locked'}
          </button>

          {/* Toggle Scroll / Zoom Mode */}
          <button
            onClick={() => setScrollMode((m) => (m === 'zoom' ? 'pan' : 'zoom'))}
            className="rounded border border-white/10 bg-white/5 px-2 py-1 text-[11px] font-medium transition-colors"
            style={{ color: scrollMode === 'zoom' ? 'var(--d-accent)' : 'var(--d-text)' }}
            title={scrollMode === 'zoom' ? 'Wheel: Zoom (click to switch to Scroll/Pan)' : 'Wheel: Scroll/Pan (click to switch to Zoom)'}
          >
            {scrollMode === 'zoom' ? '🔍 Zoom wheel' : '⇅ Scroll wheel'}
          </button>

          {/* MiniMap Toggle */}
          <button
            onClick={() => setShowMiniMap(!showMiniMap)}
            className="rounded border border-white/10 bg-white/5 px-2 py-1 text-[11px] font-medium transition-colors"
            style={{ color: showMiniMap ? 'var(--d-accent)' : 'var(--d-muted)' }}
            title="Toggle MiniMap"
          >
            🗺
          </button>
        </div>
      </div>

      <Legend
        activeKinds={activeKinds}
        onToggleKind={toggleKind}
        weakOnly={weakOnly}
        onToggleWeak={() => setWeakOnly(!weakOnly)}
      />

      {selectedId && (
        <NodeDetails
          id={selectedId}
          onClose={() => setSelectedId(null)}
          onSelect={(nextId) => {
            setSelectedId(nextId);
            void fitView({ nodes: [{ id: nextId }], padding: 0.8, duration: 400 });
          }}
        />
      )}
    </div>
  );
}

export function GraphPage() {
  const empty = useStore((s) => Object.keys(s.graph.nodes).length === 0);
  const setGraph = useStore((s) => s.setGraph);
  const setProgress = useStore((s) => s.setProgress);

  const handleLoadDemo = () => {
    setGraph(DEMO_GRAPH);
    setProgress(DEMO_PROGRESS);
  };

  return (
    <div className="h-full">
      {empty ? (
        <div className="flex h-full flex-col items-center justify-center p-6 text-center">
          <div className="mb-3 text-4xl">⬡</div>
          <h3 className="mb-1 text-base font-semibold text-white">No Graph Data Connected</h3>
          <p className="max-w-md text-xs mb-5" style={{ color: 'var(--d-muted)' }}>
            Waiting for live events from the tutor daemon (GET /api/graph). You can explore the full interactive graph page right now using demo curriculum data.
          </p>
          <button
            onClick={handleLoadDemo}
            className="rounded-lg border px-4 py-2 text-xs font-semibold text-white transition-all shadow-lg hover:brightness-125"
            style={{
              borderColor: 'var(--d-accent)',
              background: 'color-mix(in srgb, var(--d-accent) 25%, rgba(14,16,28,0.9))',
              boxShadow: '0 0 20px rgba(124,156,255,0.3)',
            }}
          >
            Load Demo Curriculum Graph
          </button>
        </div>
      ) : (
        <ReactFlowProvider>
          <GraphCanvas />
        </ReactFlowProvider>
      )}
    </div>
  );
}

