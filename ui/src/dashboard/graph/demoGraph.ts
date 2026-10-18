import type { GraphSnapshot, Progress } from '../../types/contracts';

export const DEMO_GRAPH: GraphSnapshot = {
  nodes: [
    {
      id: 'local-user',
      label: 'Student',
      props: { name: 'Demo Student', level: 'Intermediate', streak: 5 },
    },
    {
      id: 'attempt:101',
      label: 'Attempt',
      props: { lesson_id: 'binary-search', ts: Date.now() - 3600000 * 3, passed: false },
    },
    {
      id: 'attempt:102',
      label: 'Attempt',
      props: { lesson_id: 'binary-search', ts: Date.now() - 3600000, passed: false },
    },
    {
      id: 'attempt:103',
      label: 'Attempt',
      props: { lesson_id: 'binary-search', ts: Date.now() - 600000, passed: true },
    },
    {
      id: 'm:off_by_one',
      label: 'Mistake',
      props: { name: 'Off-by-One in Boundaries', lesson_id: 'binary-search', severity: 'high' },
    },
    {
      id: 'm:infinite_loop',
      label: 'Mistake',
      props: { name: 'Infinite Loop on Convergence', lesson_id: 'binary-search', severity: 'medium' },
    },
    {
      id: 'm:wrong_mid',
      label: 'Mistake',
      props: { name: 'Overflow in Mid Calculation', lesson_id: 'binary-search', severity: 'low' },
    },
    {
      id: 'c:variables',
      label: 'Concept',
      props: { name: 'Variables & Arithmetic', track: 'dsa', mastery: 0.95 },
    },
    {
      id: 'c:array_indexing',
      label: 'Concept',
      props: { name: 'Array Offsets & Boundaries', track: 'dsa', mastery: 0.32 },
    },
    {
      id: 'c:pointers',
      label: 'Concept',
      props: { name: 'Two-Pointer Navigation', track: 'dsa', mastery: 0.68 },
    },
    {
      id: 'c:loop_invariants',
      label: 'Concept',
      props: { name: 'Loop Invariants & Halting', track: 'dsa', mastery: 0.25 },
    },
    {
      id: 'c:binary_search',
      label: 'Concept',
      props: { name: 'Binary Search Algorithm', track: 'dsa', mastery: 0.55 },
    },
    {
      id: 'c:divide_and_conquer',
      label: 'Concept',
      props: { name: 'Divide and Conquer Technique', track: 'dsa', mastery: 0.42 },
    },
  ],
  edges: [
    { from: 'attempt:101', to: 'local-user', type: 'MADE' },
    { from: 'attempt:102', to: 'local-user', type: 'MADE' },
    { from: 'attempt:103', to: 'local-user', type: 'MADE' },
    { from: 'attempt:101', to: 'm:off_by_one', type: 'SHOWED' },
    { from: 'attempt:101', to: 'm:wrong_mid', type: 'SHOWED' },
    { from: 'attempt:102', to: 'm:infinite_loop', type: 'SHOWED' },
    { from: 'attempt:103', to: 'c:binary_search', type: 'RESOLVED' },
    { from: 'm:off_by_one', to: 'c:array_indexing', type: 'INVOLVES' },
    { from: 'm:infinite_loop', to: 'c:loop_invariants', type: 'INVOLVES' },
    { from: 'm:wrong_mid', to: 'c:variables', type: 'INVOLVES' },
    // Prerequisites
    { from: 'c:variables', to: 'c:array_indexing', type: 'PREREQUISITE_OF' },
    { from: 'c:variables', to: 'c:pointers', type: 'PREREQUISITE_OF' },
    { from: 'c:array_indexing', to: 'c:binary_search', type: 'PREREQUISITE_OF' },
    { from: 'c:loop_invariants', to: 'c:binary_search', type: 'PREREQUISITE_OF' },
    { from: 'c:divide_and_conquer', to: 'c:binary_search', type: 'PREREQUISITE_OF' },
  ],
};

export const DEMO_PROGRESS: Progress = {
  mode: 'dsa',
  streak: { current: 5, best: 8 },
  daily_activity: [{ date: new Date().toISOString().slice(0, 10), count: 3 }],
  skill_radar: { dsa: [{ axis: 'Binary Search', value: 0.55 }, { axis: 'Arrays', value: 0.32 }, { axis: 'Logic', value: 0.7 }] },
  concepts: [
    { id: 'c:variables', name: 'Variables & Arithmetic', track: 'dsa', mastery: 0.95 },
    { id: 'c:array_indexing', name: 'Array Offsets & Boundaries', track: 'dsa', mastery: 0.32 },
    { id: 'c:pointers', name: 'Two-Pointer Navigation', track: 'dsa', mastery: 0.68 },
    { id: 'c:loop_invariants', name: 'Loop Invariants & Halting', track: 'dsa', mastery: 0.25 },
    { id: 'c:binary_search', name: 'Binary Search Algorithm', track: 'dsa', mastery: 0.55 },
    { id: 'c:divide_and_conquer', name: 'Divide and Conquer Technique', track: 'dsa', mastery: 0.42 },
  ],
  recent_mistakes: [{ mistake_id: 'm:off_by_one', count: 2, last_seen: new Date().toISOString(), concept_ids: ['c:array_indexing'] }],
  patterns: [],
  hint_dependency: [],
  lessons: [{ id: 'binary-search', title: 'Binary Search', track: 'dsa', status: 'in_progress', progress: 0.6 }],
  recommended_next: { lesson_id: 'binary-search', reason: 'Review boundary conditions' },
  spaced_repetition_due: [],
  timeline: [],
  evolution: {
    stage: 2,
    variant: 'wizard',
    stage_name: 'Apprentice Mage',
    next_stage_name: 'Code Sorcerer',
    progress: 0.45,
    streak: 5,
    mastery_total: 3.17,
    next: { min_streak: 7, min_mastery_total: 4.0 },
  },
};
