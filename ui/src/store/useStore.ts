import { create } from 'zustand';
import { api } from '../api/rest';
import { deriveEvolution } from '../config/evolution';
import type {
  EvolutionInfo, GraphEdge, GraphNode, GraphSnapshot, GraphTrace, Mode, PetState,
  Progress, ReviewPayload, TutorOutput, UiMessage,
} from '../types/contracts';

export const edgeKey = (e: GraphEdge) => `${e.from}|${e.type}|${e.to}`;

export interface EventLogEntry { id: number; ts: number; channel: string; summary: string }

export interface InspectorEntry { trace: GraphTrace; mistake_id: string; hint_level: number; question: string; at: number }

interface Highlight { nodeIds: string[]; edgeKeys: string[]; at: number; reason?: string }

interface StoreState {
  connected: boolean;
  mode: Mode;
  petState: PetState;
  bubble: { data: TutorOutput; at: number } | null;
  progress: Progress | null;
  evolution: EvolutionInfo | null;
  graph: { nodes: Record<string, GraphNode>; edges: Record<string, GraphEdge> };
  highlight: Highlight | null;
  review: Record<string, ReviewPayload>;
  reviewPrev: Record<string, ReviewPayload>;
  activeDesignId: string | null;
  confetti: { tick: number; count: number };
  inspector: { enabled: boolean; drawerOpen: boolean; trace: GraphTrace | null; at: number; history: InspectorEntry[] };
  events: EventLogEntry[];
  mistakeRepeat: { id: string | null; count: number };
  issues: Record<string, number>; // contract warnings: what the daemon sent that deviates from the contract

  bubbleOpen: boolean;
  toggleBubble(): void;
  openBubble(): void;
  closeBubble(): void;
  reportIssues(list: string[]): void;
  setConnected(v: boolean): void;
  setProgress(p: Progress): void;
  setGraph(g: GraphSnapshot): void;
  setMode(m: Mode): void;
  setReview(r: ReviewPayload): void;
  setActiveDesign(id: string): void;
  setInspectorEnabled(enabled: boolean): Promise<void>;
  closeInspectorDrawer(): void;
  applyBatch(msgs: UiMessage[]): void;
}

let eventSeq = 0;

function summarize(m: UiMessage): string {
  switch (m.channel) {
    case 'pet_state': return m.payload.state;
    case 'bubble': return `L${m.payload.hint_level} ${m.payload.mistake_id}`;
    case 'graph_update': return `${m.payload.nodes.length}n/${m.payload.edges.length}e ${m.payload.highlight.reason ?? ''}`.trim();
    case 'mastery_update': return `${m.payload.concept_id} ${m.payload.delta >= 0 ? '+' : ''}${m.payload.delta.toFixed(2)}`;
    case 'confetti': return m.payload.reason ?? 'confetti';
    case 'mode': return m.payload.mode;
    case 'review': return `${m.payload.design_id} (${m.payload.axes.length} axes)`;
  }
}

type Slice = Pick<StoreState,
  'mode' | 'petState' | 'bubble' | 'progress' | 'evolution' | 'graph' | 'highlight' |
  'review' | 'reviewPrev' | 'activeDesignId' | 'confetti' | 'inspector' | 'events' | 'mistakeRepeat' | 'issues'>;

function reduce(s: Slice, m: UiMessage, now: number): Slice {
  const next: Slice = { ...s, events: [{ id: ++eventSeq, ts: now, channel: m.channel, summary: summarize(m) }, ...s.events].slice(0, 60) };

  switch (m.channel) {
    case 'pet_state':
      next.petState = m.payload.state;
      break;
    case 'bubble':
      next.bubble = { data: m.payload, at: now };
      next.mistakeRepeat = {
        id: m.payload.mistake_id,
        count: s.mistakeRepeat.id === m.payload.mistake_id ? s.mistakeRepeat.count + 1 : 1,
      };
      next.petState = m.payload.pet_state;
      next.inspector = {
        ...s.inspector,
        trace: m.payload.graph_trace,
        at: now,
        history: [
          { trace: m.payload.graph_trace, mistake_id: m.payload.mistake_id, hint_level: m.payload.hint_level, question: m.payload.question, at: now },
          ...s.inspector.history,
        ].slice(0, 20),
        drawerOpen: s.inspector.enabled ? true : s.inspector.drawerOpen,
      };
      break;
    case 'graph_update': {
      const nodes = { ...s.graph.nodes };
      const edges = { ...s.graph.edges };
      m.payload.nodes.forEach((n) => { nodes[n.id] = n; });
      m.payload.edges.forEach((e) => { edges[edgeKey(e)] = e; });
      next.graph = { nodes, edges };
      next.highlight = {
        nodeIds: m.payload.highlight.node_ids,
        edgeKeys: m.payload.highlight.edges.map(edgeKey),
        at: now,
        reason: m.payload.highlight.reason,
      };
      break;
    }
    case 'mastery_update': {
      if (s.progress) {
        const concepts = s.progress.concepts.map((c) => (c.id === m.payload.concept_id ? { ...c, mastery: m.payload.mastery } : c));
        // daemon-provided evolution wins; otherwise derive from streak + mastery
        const evolution = m.payload.evolution ?? deriveEvolution(s.progress.streak.current, concepts);
        next.progress = { ...s.progress, concepts, evolution };
        next.evolution = evolution;
      } else if (m.payload.evolution) {
        next.evolution = m.payload.evolution;
      }
      break;
    }
    case 'confetti':
      next.confetti = { tick: s.confetti.tick + 1, count: m.payload.count ?? 120 };
      next.mistakeRepeat = { id: null, count: 0 }; // tests passed: streak of the same mistake is over
      break;
    case 'mode':
      next.mode = m.payload.mode;
      break;
    case 'review':
      if (s.review[m.payload.design_id]) next.reviewPrev = { ...s.reviewPrev, [m.payload.design_id]: s.review[m.payload.design_id] };
      next.review = { ...s.review, [m.payload.design_id]: m.payload };
      next.activeDesignId = m.payload.design_id;
      break;
  }
  return next;
}

export const useStore = create<StoreState>((set, get) => ({
  connected: false,
  mode: 'dsa',
  petState: 'idle',
  bubble: null,
  progress: null,
  evolution: null,
  graph: { nodes: {}, edges: {} },
  highlight: null,
  review: {},
  reviewPrev: {},
  activeDesignId: null,
  confetti: { tick: 0, count: 0 },
  inspector: { enabled: false, drawerOpen: false, trace: null, at: 0, history: [] },
  events: [],
  mistakeRepeat: { id: null, count: 0 },
  issues: {},

  bubbleOpen: true,
  toggleBubble: () => set((s) => ({ bubbleOpen: !s.bubbleOpen })),
  openBubble: () => set({ bubbleOpen: true }),
  closeBubble: () => set({ bubbleOpen: false }),

  reportIssues: (list) => set((s) => {
    const issues = { ...s.issues };
    for (const i of list) {
      if (!(i in issues)) console.warn('[contract]', i);
      issues[i] = (issues[i] ?? 0) + 1;
    }
    return { issues };
  }),
  setConnected: (connected) => set({ connected }),
  setProgress: (progress) => set({ progress, evolution: progress.evolution, ...(progress.mode ? { mode: progress.mode } : {}) }),
  setGraph: (g) => set({
    graph: {
      nodes: Object.fromEntries(g.nodes.map((n) => [n.id, n])),
      edges: Object.fromEntries(g.edges.map((e) => [edgeKey(e), e])),
    },
  }),
  setMode: (mode) => set({ mode }),
  setActiveDesign: (activeDesignId) => set({ activeDesignId }),
  setReview: (r) => set((s) => ({ review: { ...s.review, [r.design_id]: r }, activeDesignId: s.activeDesignId ?? r.design_id })),

  async setInspectorEnabled(enabled) {
    set((s) => ({ inspector: { ...s.inspector, enabled, drawerOpen: enabled ? s.inspector.drawerOpen : false } }));
    try { await api.inspector(enabled); } catch { /* daemon may be down; local flag still applies */ }
  },
  closeInspectorDrawer: () => set((s) => ({ inspector: { ...s.inspector, drawerOpen: false } })),

  applyBatch(msgs) {
    const now = Date.now();
    const s = get();
    const slice: Slice = {
      mode: s.mode, petState: s.petState, bubble: s.bubble, progress: s.progress,
      evolution: s.evolution, graph: s.graph, highlight: s.highlight, review: s.review, reviewPrev: s.reviewPrev,
      activeDesignId: s.activeDesignId, confetti: s.confetti, inspector: s.inspector, events: s.events,
      mistakeRepeat: s.mistakeRepeat, issues: s.issues,
    };
    set(msgs.reduce((acc, m) => reduce(acc, m, now), slice));
  },
}));
