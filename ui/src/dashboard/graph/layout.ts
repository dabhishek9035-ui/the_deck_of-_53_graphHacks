import type { GraphEdge, GraphNode } from '../../types/contracts';

// Left-to-right layered layout: Student -> Attempt -> Mistake -> Concept.
// Uses barycenter heuristic (connection-aware vertical sorting) to drastically reduce edge crossings.
export const COLUMN: Record<string, number> = { Student: 0, Attempt: 1, Mistake: 2, Concept: 3 };
export const COL_X = [60, 420, 800, 1220, 1640];
export const ROW: Record<string, number> = { Student: 96, Attempt: 90, Mistake: 110, Concept: 76 };
export const MAX_ATTEMPTS = 10;

export const colIndex = (label: string) => COLUMN[label] ?? 4;

const attemptOrder = (n: GraphNode): [number, number] => {
  const seq = Number(/(\d+)$/.exec(n.id)?.[1] ?? 0);
  const ts = typeof n.props.ts === 'number' ? n.props.ts : 0;
  return [ts, seq];
};

export interface LaneHeader {
  id: string;
  label: string;
  x: number;
  width: number;
  count: number;
  color: string;
}

export interface Layout {
  pos: Map<string, { x: number; y: number }>;
  visible: Set<string>;
  lanes: LaneHeader[];
  bounds: { width: number; height: number };
}

export function computeLayout(nodes: GraphNode[], keep: Set<string>, edges: GraphEdge[] = []): Layout {
  const by: Record<string, GraphNode[]> = {};
  for (const n of nodes) (by[n.label] ??= []).push(n);

  // 1. Sort attempts chronologically (most recent on top)
  const attempts = (by.Attempt ?? []).sort((a, b) => {
    const [ta, sa] = attemptOrder(a), [tb, sb] = attemptOrder(b);
    return tb - ta || sb - sa;
  });
  const recent = new Set(attempts.slice(0, MAX_ATTEMPTS).map((n) => n.id));
  const shownAttempts = attempts.filter((n) => recent.has(n.id) || keep.has(n.id));

  // Build attempt index lookup for barycenter computation
  const attemptIndex = new Map<string, number>();
  shownAttempts.forEach((a, idx) => attemptIndex.set(a.id, idx));

  // 2. Sort mistakes by barycenter of connected attempts
  const mistakeAttemptMap = new Map<string, number[]>();
  for (const e of edges) {
    if (e.type === 'SHOWED' && attemptIndex.has(e.from)) {
      const list = mistakeAttemptMap.get(e.to) ?? [];
      list.push(attemptIndex.get(e.from)!);
      mistakeAttemptMap.set(e.to, list);
    }
  }

  const mistakeBarycenter = (id: string): number => {
    const list = mistakeAttemptMap.get(id);
    if (!list || !list.length) return 999;
    return list.reduce((a, b) => a + b, 0) / list.length;
  };

  const mistakes = (by.Mistake ?? []).sort((a, b) => {
    const ba = mistakeBarycenter(a.id), bb = mistakeBarycenter(b.id);
    return ba - bb || a.id.localeCompare(b.id);
  });

  // Build mistake index lookup for concept barycenter computation
  const mistakeIndex = new Map<string, number>();
  mistakes.forEach((m, idx) => mistakeIndex.set(m.id, idx));

  // 3. Sort concepts by track and barycenter of connected mistakes
  const conceptMistakeMap = new Map<string, number[]>();
  for (const e of edges) {
    if ((e.type === 'INVOLVES' || e.type === 'TARGETS') && mistakeIndex.has(e.from)) {
      const list = conceptMistakeMap.get(e.to) ?? [];
      list.push(mistakeIndex.get(e.from)!);
      conceptMistakeMap.set(e.to, list);
    }
  }

  const conceptBarycenter = (id: string): number => {
    const list = conceptMistakeMap.get(id);
    if (!list || !list.length) return 999;
    return list.reduce((a, b) => a + b, 0) / list.length;
  };

  const concepts = (by.Concept ?? []).sort((a, b) => {
    const trackA = String(a.props.track ?? '');
    const trackB = String(b.props.track ?? '');
    const trackDiff = trackA.localeCompare(trackB);
    if (trackDiff !== 0) return trackDiff;
    const ba = conceptBarycenter(a.id), bb = conceptBarycenter(b.id);
    return ba - bb || a.id.localeCompare(b.id);
  });

  const students = by.Student ?? [];
  const others = Object.keys(by).filter((k) => !(k in COLUMN)).flatMap((k) => by[k].sort((a, b) => a.id.localeCompare(b.id)));

  const startY = 80;
  const H = Math.max(
    concepts.length * ROW.Concept,
    shownAttempts.length * ROW.Attempt,
    mistakes.length * ROW.Mistake,
    450,
  );

  const pos = new Map<string, { x: number; y: number }>();
  const visible = new Set<string>();
  const put = (list: GraphNode[], x: number, row: number, originY: number) => {
    list.forEach((n, i) => {
      pos.set(n.id, { x, y: originY + i * row });
      visible.add(n.id);
    });
  };

  // Center students alongside attempts; start attempts/mistakes/concepts cleanly from top
  const studentStartY = shownAttempts.length > 0 ? startY + Math.max(0, (Math.min(shownAttempts.length, 3) * ROW.Attempt) / 2 - 40) : startY;
  put(students, COL_X[0], ROW.Student, studentStartY);
  put(shownAttempts, COL_X[1], ROW.Attempt, startY);
  put(mistakes, COL_X[2], ROW.Mistake, startY);
  put(concepts, COL_X[3], ROW.Concept, startY);
  put(others, COL_X[4], ROW.Concept, startY);

  const lanes: LaneHeader[] = [
    { id: 'Student', label: 'Student', x: COL_X[0], width: 230, count: students.length, color: '#b388ff' },
    { id: 'Attempt', label: 'Attempts', x: COL_X[1], width: 230, count: shownAttempts.length, color: '#7fd4ff' },
    { id: 'Mistake', label: 'Mistakes', x: COL_X[2], width: 230, count: mistakes.length, color: '#ff6b81' },
    { id: 'Concept', label: 'Concepts', x: COL_X[3], width: 230, count: concepts.length, color: '#7c9cff' },
  ];

  return {
    pos,
    visible,
    lanes,
    bounds: { width: COL_X[3] + 300, height: startY + H + 80 },
  };
}
