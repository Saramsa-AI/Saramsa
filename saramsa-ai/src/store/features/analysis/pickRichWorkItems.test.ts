import { describe, it, expect } from 'vitest';
import { pickRichWorkItems } from './analysisSlice';

// The analysis payload repeats the same work items in three shapes. Taking the
// phrasing-only one blanked the priority/type badges, made the priority sort a
// no-op, stopped pushed items from greying out, and pushed the `candidate_id`
// column into `id` — which the review/push endpoints do not resolve.
const PHRASING_ONLY = [
  {
    title: 'Eliminate room odors',
    description: 'd',
    candidate_id: '72cfd1ff-6204-4695-aeb2-7cad11e9bcdc',
    business_value: 'bv',
    acceptance_criteria: 'ac',
  },
];

const PERSISTED = [
  {
    id: 'c1a191c5-c880-46ad-8d99-b5fce343a66f',
    candidate_id: '72cfd1ff-6204-4695-aeb2-7cad11e9bcdc',
    title: 'Eliminate room odors',
    description: 'd',
    type: 'task',
    priority: 'high',
    status: 'pending',
    push_status: 'not_pushed',
  },
];

describe('pickRichWorkItems', () => {
  it('prefers the persisted rows over the phrasing-only shapes', () => {
    const picked = pickRichWorkItems({
      pipeline_work_items: PHRASING_ONLY,
      work_items: PHRASING_ONLY,
      userStories: { work_items: PERSISTED },
    });
    expect(picked).toBe(PERSISTED);
    expect(picked[0].priority).toBe('high');
    expect(picked[0].type).toBe('task');
    // Must be the row id, not the candidate_id column — the review/approve/
    // push endpoints filter on the row's own id.
    expect(picked[0].id).toBe('c1a191c5-c880-46ad-8d99-b5fce343a66f');
  });

  it('accepts the snake_case user_stories spelling', () => {
    const picked = pickRichWorkItems({
      pipeline_work_items: PHRASING_ONLY,
      user_stories: { work_items: PERSISTED },
    });
    expect(picked[0].priority).toBe('high');
  });

  it('falls back to the phrasing-only shape when nothing richer exists', () => {
    expect(pickRichWorkItems({ pipeline_work_items: PHRASING_ONLY })).toBe(PHRASING_ONLY);
    expect(pickRichWorkItems({ work_items: PHRASING_ONLY })).toBe(PHRASING_ONLY);
  });

  it('skips empty arrays rather than treating them as a valid choice', () => {
    const picked = pickRichWorkItems({
      userStories: { work_items: [] },
      pipeline_work_items: PHRASING_ONLY,
    });
    expect(picked).toBe(PHRASING_ONLY);
  });

  it('returns [] for missing, null, or malformed input', () => {
    expect(pickRichWorkItems(undefined)).toEqual([]);
    expect(pickRichWorkItems(null)).toEqual([]);
    expect(pickRichWorkItems({})).toEqual([]);
    expect(pickRichWorkItems({ work_items: 'not-an-array' })).toEqual([]);
  });
});
