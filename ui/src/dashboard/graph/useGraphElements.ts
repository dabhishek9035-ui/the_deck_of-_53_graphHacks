import { useMemo, useRef } from 'react';
import { MarkerType, type Edge } from '@xyflow/react';
import { edgeKey, useStore } from '../../store/useStore';
import { humanize, WEAK } from '../util';
import type { GFlowNode } from './GNode';
import { conceptKey, nodeTitle } from './ids';
import { colIndex, computeLayout, type LaneHeader } from './layout';

export interface Lit { nodeIds: string[]; edgeKeys: string[] }

export interface UseGraphElementsOptions {
  showPrereq: boolean;
  selectedId: string | null;
  hoveredId?: string | null;
  lit: Lit | null;
  activeKinds?: Set<string>;
  trackFilter?: string;
  weakOnly?: boolean;
  searchQuery?: string;
  onNodeHover?: (id: string | null) => void;
}

const EDGE_COLOR: Record<string, string> = {
  MADE: '#b388ff', SHOWED: '#ff6b81', RESOLVED: '#4ade80',
  INVOLVES: '#7c9cff', TARGETS: '#7c9cff', PREREQUISITE_OF: '#9aa3c4', REQUIRES: '#9aa3c4',
};
const STAGGER = 220;

export function useGraphElements(
  optionsOrShowPrereq: boolean | UseGraphElementsOptions,
  legacySelectedId: string | null = null,
  legacyLit: Lit | null = null,
): { nodes: GFlowNode[]; edges: Edge[]; lanes: LaneHeader[]; bounds: { width: number; height: number } } {
  const opts: UseGraphElementsOptions = typeof optionsOrShowPrereq === 'boolean'
    ? { showPrereq: optionsOrShowPrereq, selectedId: legacySelectedId, lit: legacyLit }
    : optionsOrShowPrereq;

  const {
    showPrereq,
    selectedId,
    hoveredId = null,
    lit,
    activeKinds,
    trackFilter = 'all',
    weakOnly = false,
    searchQuery = '',
    onNodeHover,
  } = opts;

  const graph = useStore((s) => s.graph);
  const concepts = useStore((s) => s.progress?.concepts);
  const nodeCache = useRef(new Map<string, { sig: string; node: GFlowNode }>());
  const edgeCache = useRef(new Map<string, { sig: string; edge: Edge }>());

  return useMemo(() => {
    const litNodes = new Set(lit?.nodeIds ?? []);
    const litEdges = new Set(lit?.edgeKeys ?? []);
    const live = new Map((concepts ?? []).map((c) => [c.id, c.mastery]));
    const liveByName = new Map((concepts ?? []).map((c) => [c.name.toLowerCase(), c.mastery]));
    const all = Object.values(graph.nodes);
    const allEdges = Object.values(graph.edges);

    // 1. Compute deterministic barycenter layout
    const layout = computeLayout(all, litNodes, allEdges);
    const { pos, lanes, bounds } = layout;

    // 2. Compute neighborhood / focus set for clutter elimination
    const focusId = hoveredId ?? selectedId;
    let focusNodes = new Set<string>();
    let focusEdges = new Set<string>();

    if (focusId && graph.nodes[focusId]) {
      focusNodes.add(focusId);

      // Direct 1-hop connections
      for (const e of allEdges) {
        if (e.from === focusId) {
          focusNodes.add(e.to);
          focusEdges.add(edgeKey(e));
        } else if (e.to === focusId) {
          focusNodes.add(e.from);
          focusEdges.add(edgeKey(e));
        }
      }

      // 2-hop causal chain extension (Attempt <-> Mistake <-> Concept)
      const primaryNode = graph.nodes[focusId];
      if (primaryNode.label === 'Attempt') {
        // Attempt -> Mistake -> Concept
        for (const e of allEdges) {
          if (focusNodes.has(e.from) && (e.type === 'INVOLVES' || e.type === 'TARGETS')) {
            focusNodes.add(e.to);
            focusEdges.add(edgeKey(e));
          }
        }
      } else if (primaryNode.label === 'Mistake') {
        // Mistake -> Concept & Mistake <- Attempt
        for (const e of allEdges) {
          if (focusNodes.has(e.to) && e.type === 'SHOWED') {
            focusNodes.add(e.from);
            focusEdges.add(edgeKey(e));
          }
        }
      } else if (primaryNode.label === 'Concept') {
        // Concept <- Mistake <- Attempt
        for (const e of allEdges) {
          if (focusNodes.has(e.to) && e.type === 'SHOWED') {
            focusNodes.add(e.from);
            focusEdges.add(edgeKey(e));
          }
        }
      }
    }

    const hasFocus = focusNodes.size > 0;
    const cleanQuery = searchQuery.trim().toLowerCase();

    // 3. Build Nodes
    const nodes: GFlowNode[] = [];
    for (const n of all) {
      const p = pos.get(n.id);
      if (!p) continue;

      // Filter by Kind
      if (activeKinds && !activeKinds.has(n.label)) continue;

      const isConcept = n.label === 'Concept';
      const title = nodeTitle(n, humanize);
      const m = isConcept
        ? (live.get(conceptKey(n)) ?? liveByName.get(title.toLowerCase()) ?? (typeof n.props.mastery === 'number' ? Math.min(1, n.props.mastery > 1 ? n.props.mastery / 100 : n.props.mastery) : undefined))
        : undefined;

      const isWeak = m !== undefined && m < WEAK;

      // Filter by Weak Only
      if (weakOnly && isConcept && !isWeak) continue;

      // Filter by Track
      if (trackFilter !== 'all' && isConcept) {
        const trk = String(n.props.track ?? '').toLowerCase();
        if (trk && trk !== trackFilter.toLowerCase()) continue;
      }

      // Search match
      const subtitle = n.label === 'Attempt'
        ? String(n.props.lesson_id ?? '')
        : n.label === 'Concept'
          ? humanize(String(n.props.track ?? ''))
          : n.label === 'Mistake'
            ? humanize(String(n.props.lesson_id ?? ''))
            : undefined;

      const matchesSearch = !cleanQuery || title.toLowerCase().includes(cleanQuery) || (subtitle && subtitle.toLowerCase().includes(cleanQuery)) || n.id.toLowerCase().includes(cleanQuery);
      if (cleanQuery && !matchesSearch && !focusNodes.has(n.id)) continue;

      const isLit = litNodes.has(n.id);
      const passed = n.label === 'Attempt' ? n.props.passed === true : undefined;
      const delay = isLit ? colIndex(n.label) * STAGGER : 0;
      const selected = n.id === selectedId;
      const hovered = n.id === hoveredId;
      const focused = focusNodes.has(n.id);
      const dimmed = hasFocus && !focused;

      const sig = [p.x, p.y, title, subtitle, m?.toFixed(3), isLit, delay, passed, selected, hovered, focused, dimmed].join('|');
      const prev = nodeCache.current.get(n.id);
      if (prev && prev.sig === sig) {
        nodes.push(prev.node);
        continue;
      }

      const node: GFlowNode = {
        id: n.id,
        type: 'gnode',
        position: p,
        selected,
        data: {
          kind: n.label,
          title,
          subtitle,
          mastery: m,
          weak: isWeak,
          lit: isLit,
          delay,
          passed,
          dimmed,
          focused,
          hovered,
          onHover: onNodeHover,
        },
      };
      nodeCache.current.set(n.id, { sig, node });
      nodes.push(node);
    }

    const visibleNodeIds = new Set(nodes.map((n) => n.id));

    // 4. Build Edges
    const edges: Edge[] = [];
    for (const e of allEdges) {
      if (!visibleNodeIds.has(e.from) || !visibleNodeIds.has(e.to)) continue;

      const prereq = e.type === 'PREREQUISITE_OF' || e.type === 'REQUIRES';
      if (prereq && !showPrereq) continue;

      const key = edgeKey(e);
      const isLit = litEdges.has(key);
      const isFocused = focusEdges.has(key);
      const isDimmed = hasFocus && !isFocused;

      const srcLabel = graph.nodes[e.from]?.label ?? '';
      const delay = colIndex(srcLabel) * STAGGER + STAGGER / 2;
      const sig = [isLit, isFocused, isDimmed, delay].join('|');
      const prev = edgeCache.current.get(key);
      if (prev && prev.sig === sig) {
        edges.push(prev.edge);
        continue;
      }

      const baseColor = EDGE_COLOR[e.type] ?? 'rgba(255,255,255,0.35)';
      const color = isLit ? '#ffd666' : isFocused ? '#7c9cff' : baseColor;

      const edge: Edge = {
        id: key,
        source: e.from,
        target: e.to,
        sourceHandle: prereq ? 'ps' : 'out',
        targetHandle: prereq ? 'pt' : 'in',
        type: prereq ? 'smoothstep' : 'default',
        className: [
          isLit ? 'edge-lit' : '',
          isFocused ? 'edge-focused' : '',
          isDimmed ? 'edge-dimmed' : '',
        ].filter(Boolean).join(' ') || undefined,
        animated: isLit || isFocused,
        style: {
          stroke: isLit ? color : isDimmed ? 'rgba(255,255,255,0.08)' : color,
          strokeWidth: isLit || isFocused ? 2.5 : prereq ? 1.2 : 1.6,
          strokeDasharray: prereq && !isLit ? '5 5' : undefined,
          opacity: isDimmed ? 0.12 : 1,
          transition: 'stroke 0.25s, stroke-width 0.25s, opacity 0.25s',
          ['--delay' as string]: `${delay}ms`,
        },
        markerEnd: !prereq
          ? {
              type: MarkerType.ArrowClosed,
              width: 14,
              height: 14,
              color: isDimmed ? 'rgba(255,255,255,0.1)' : color,
            }
          : undefined,
      };

      edgeCache.current.set(key, { sig, edge });
      edges.push(edge);
    }

    return { nodes, edges, lanes, bounds };
  }, [graph, concepts, showPrereq, selectedId, hoveredId, lit, activeKinds, trackFilter, weakOnly, searchQuery, onNodeHover]);
}

