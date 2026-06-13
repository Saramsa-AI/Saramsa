import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest'
import { configureStore } from '@reduxjs/toolkit'

// apiRequest is the only side-effecting dependency the tested thunks use.
vi.mock('@/lib/apiRequest', () => ({ apiRequest: vi.fn() }))

import { apiRequest } from '@/lib/apiRequest'
import analysisReducer, {
  resolveAnalyzingTask,
  replaceInHistory,
  setTaskIdForEntry,
  cancelAnalysisTask,
  generateUserStories,
  submitUserStories,
  analyzeComments,
  retriggerAnalysis,
  resumeInFlightTask,
  ingestFile,
} from './analysisSlice'

// Full initial state (reducer with unknown action), to spread + override.
const baseState = () => analysisReducer(undefined, { type: '@@init' } as any)
const entry = (over: any = {}) => ({
  id: 'e1', analysis_date: '', comments_count: 0, positive_pct: 0, status: 'analyzing', ...over,
})

beforeEach(() => vi.clearAllMocks())

describe('analysisSlice reducers (restored)', () => {
  test('resolveAnalyzingTask resolves a placeholder to terminal + releases selection + clears current task', () => {
    const start = {
      ...baseState(),
      analysisHistory: [entry({ id: 'analyzing_t1' })],
      selectedAnalysisId: 'analyzing_t1',
      currentTaskId: 't1',
      currentTaskStatus: 'analyzing' as const,
    }
    const next = analysisReducer(start, resolveAnalyzingTask({ placeholderId: 'analyzing_t1', taskId: 't1', historyStatus: 'cancelled' }))
    expect(next.analysisHistory[0].status).toBe('cancelled')
    expect(next.selectedAnalysisId).toBeNull()
    expect(next.currentTaskId).toBeNull()
    expect(next.currentTaskStatus).toBe('idle')
  })

  test('resolveAnalyzingTask swaps the placeholder id to the real insight id', () => {
    const start = {
      ...baseState(),
      analysisHistory: [entry({ id: 'analyzing_t2' })],
      selectedAnalysisId: 'analyzing_t2',
    }
    const next = analysisReducer(start, resolveAnalyzingTask({ placeholderId: 'analyzing_t2', historyStatus: 'completed', insightId: 'insight_abc' }))
    expect(next.analysisHistory[0].id).toBe('insight_abc')
    expect(next.analysisHistory[0].status).toBe('completed')
    expect(next.selectedAnalysisId).toBe('insight_abc')
  })

  test('replaceInHistory swaps the matching row, prepends when missing', () => {
    const start = { ...baseState(), analysisHistory: [entry({ id: 'analyzing_x' })] }
    const next = analysisReducer(start, replaceInHistory({ oldId: 'analyzing_x', entry: entry({ id: 'insight_x', status: 'completed' }) }))
    expect(next.analysisHistory).toHaveLength(1)
    expect(next.analysisHistory[0].id).toBe('insight_x')

    const next2 = analysisReducer(next, replaceInHistory({ oldId: 'missing', entry: entry({ id: 'insight_y', status: 'completed' }) }))
    expect(next2.analysisHistory).toHaveLength(2)
    expect(next2.analysisHistory[0].id).toBe('insight_y')
  })

  test('setTaskIdForEntry attaches the celery task id to the matching row', () => {
    const start = { ...baseState(), analysisHistory: [entry({ id: 'analyzing_z' })] }
    const next = analysisReducer(start, setTaskIdForEntry({ tempId: 'analyzing_z', taskId: 'celery-1' }))
    expect(next.analysisHistory[0].task_id).toBe('celery-1')
  })
})

describe('analysisSlice thunks (restored)', () => {
  const invoke = (thunk: any, dispatch = vi.fn()) =>
    thunk(dispatch, vi.fn(() => ({ analysis: {} })), undefined)

  test('generateUserStories POSTs the creation endpoint and returns work items', async () => {
    ;(apiRequest as any).mockResolvedValue({ data: { data: { work_items: [{ id: 'wi1' }] } } })
    const res: any = await invoke(generateUserStories({ analysisData: { x: 1 }, comments: ['a'], platform: 'asana', projectId: 'p1' }))
    expect(apiRequest).toHaveBeenCalledWith(
      'post', '/insights/user-story-creation/',
      expect.objectContaining({ platform: 'asana', project_id: 'p1' }),
      true, false, expect.objectContaining({ timeout: 180000 }),
    )
    expect(res.type).toContain('/fulfilled')
    expect(res.payload.work_items[0].id).toBe('wi1')
  })

  test('submitUserStories POSTs the submission endpoint with the platform', async () => {
    ;(apiRequest as any).mockResolvedValue({ data: { success: true } })
    const res: any = await invoke(submitUserStories({ userId: 'u1', projectId: 'p1', userStories: [{}], platform: 'jira' }))
    expect(apiRequest).toHaveBeenCalledWith(
      'post', '/insights/user-story-submission/',
      expect.objectContaining({ user_id: 'u1', project_id: 'p1', platform: 'jira' }),
      true, false,
    )
    expect(res.payload.success).toBe(true)
  })

  test('cancelAnalysisTask POSTs task-cancel and dispatches the resolve action (cancelled)', async () => {
    ;(apiRequest as any).mockResolvedValue({ data: { success: true } })
    const dispatch = vi.fn()
    const res: any = await cancelAnalysisTask({ taskId: 'celery-9', tempId: 'analyzing_9' })(
      dispatch, vi.fn(() => ({ analysis: {} })), undefined,
    )
    expect(apiRequest).toHaveBeenCalledWith('post', '/insights/task-cancel/celery-9/', undefined, true)
    const resolveCall = dispatch.mock.calls.find((c: any) => String(c[0]?.type).includes('resolveAnalyzingTask'))
    expect(resolveCall).toBeTruthy()
    expect(resolveCall![0].payload).toMatchObject({ placeholderId: 'analyzing_9', historyStatus: 'cancelled' })
    expect(res.type).toContain('/fulfilled')
  })

  test('generateUserStories rejects with the API error message', async () => {
    ;(apiRequest as any).mockRejectedValue({ response: { status: 400, data: { error: 'bad input' } } })
    const res: any = await invoke(generateUserStories({ analysisData: {}, comments: [], platform: 'jira' }))
    expect(res.type).toContain('/rejected')
    expect(res.payload).toBe('bad input')
  })
})

// The polling thunks go through waitForAnalysisTask (setInterval). We drive them
// with a real store (so nested dispatches like fetchAnalysisHistory run) + fake
// timers, and route apiRequest by URL.
describe('analysisSlice polling thunks', () => {
  const makeStore = () => configureStore({ reducer: { analysis: analysisReducer } })

  // Match apiRequest(method, url, ...) by URL substring → response.
  const route = (handlers: Array<[string, any]>) => {
    ;(apiRequest as any).mockImplementation((_method: string, url: string) => {
      for (const [pattern, resp] of handlers) {
        if (url.includes(pattern)) return Promise.resolve(resp)
      }
      return Promise.resolve({ data: { data: {} } })
    })
  }

  beforeEach(() => { vi.clearAllMocks(); vi.useFakeTimers() })
  afterEach(() => { vi.useRealTimers() })

  test('analyzeComments POSTs /analyze/, polls to SUCCESS, resolves with the insight', async () => {
    route([
      ['/insights/analyze/', { data: { data: { task_id: 'task-1' } } }],
      ['/insights/task-status/', { data: { data: { status: 'SUCCESS', result: { insight_id: 'insight_1' } } } }],
      ['/feedback/analysis/', { data: { data: { id: 'insight_1', analysisData: { x: 1 } } } }],
    ])
    const store = makeStore()
    const p = store.dispatch(analyzeComments({ comments: ['a'], projectId: 'p1' }) as any)
    await vi.advanceTimersByTimeAsync(2000)
    const res: any = await p
    expect(res.type).toContain('/fulfilled')
    expect(res.payload.id).toBe('insight_1')
    expect(res.payload.partial).toBe(false)
    expect(apiRequest).toHaveBeenCalledWith('post', '/insights/analyze/', expect.objectContaining({ comments: ['a'], project_id: 'p1' }), true, false)
  })

  test('waitForAnalysisTask treats PARTIAL as terminal (no 30-min hang)', async () => {
    route([
      ['/insights/analyze/', { data: { data: { task_id: 'task-2' } } }],
      ['/insights/task-status/', { data: { data: { status: 'PARTIAL', result: { insight_id: 'insight_2' } } } }],
      ['/feedback/analysis/', { data: { data: { id: 'insight_2' } } }],
    ])
    const store = makeStore()
    const p = store.dispatch(analyzeComments({ comments: ['a'] }) as any)
    await vi.advanceTimersByTimeAsync(2000)
    const res: any = await p
    expect(res.type).toContain('/fulfilled')
    expect(res.payload.partial).toBe(true)
  })

  test('analyzeComments rejects when the task FAILS', async () => {
    route([
      ['/insights/analyze/', { data: { data: { task_id: 'task-3' } } }],
      ['/insights/task-status/', { data: { data: { status: 'FAILED', error: 'kaboom' } } }],
    ])
    const store = makeStore()
    const p = store.dispatch(analyzeComments({ comments: ['a'] }) as any)
    await vi.advanceTimersByTimeAsync(2000)
    const res: any = await p
    expect(res.type).toContain('/rejected')
    expect(res.payload).toContain('kaboom')
  })

  test('retriggerAnalysis POSTs retrigger, polls, refreshes history, selects the run', async () => {
    route([
      ['/retrigger/', { data: { data: { task_id: 'task-r' } } }],
      ['/insights/task-status/', { data: { data: { status: 'SUCCESS', result: { insight_id: 'insight_r' } } } }],
      ['/feedback/analysis/', { data: { data: { id: 'insight_r' } } }],
      ['/feedback/history/list/', { data: { data: { analyses: [{ id: 'insight_r', status: 'completed', created_at: '' }] } } }],
    ])
    const store = makeStore()
    const p = store.dispatch(retriggerAnalysis({ analysisId: 'insight_r', projectId: 'p1' }) as any)
    await vi.advanceTimersByTimeAsync(2000)
    const res: any = await p
    expect(res.type).toContain('/fulfilled')
    expect(apiRequest).toHaveBeenCalledWith('post', '/insights/analyses/insight_r/retrigger/', undefined, true)
    expect(store.getState().analysis.selectedAnalysisId).toBe('insight_r')
    expect(store.getState().analysis.analysisHistory.some(e => e.id === 'insight_r')).toBe(true)
  })

  test('ingestFile POSTs /ingest/ (multipart), polls, refreshes history', async () => {
    route([
      ['/insights/ingest/', { data: { data: { task_id: 'task-i', comments: ['c1'] } } }],
      ['/insights/task-status/', { data: { data: { status: 'SUCCESS', result: { insight_id: 'insight_i' } } } }],
      ['/feedback/analysis/', { data: { data: { id: 'insight_i' } } }],
      ['/feedback/history/list/', { data: { data: { analyses: [] } } }],
    ])
    const store = makeStore()
    const file = new File(['x'], 'f.csv', { type: 'text/csv' })
    const p = store.dispatch(ingestFile({ file, projectId: 'p1' }) as any)
    await vi.advanceTimersByTimeAsync(2000)
    const res: any = await p
    expect(res.type).toContain('/fulfilled')
    expect(res.payload.id).toBe('insight_i')
    expect(apiRequest).toHaveBeenCalledWith('post', '/insights/ingest/', expect.any(FormData), true, true)
  })

  test('resumeInFlightTask polls an existing task to completion + refreshes history', async () => {
    route([
      ['/insights/task-status/', { data: { data: { status: 'SUCCESS', result: { insight_id: 'insight_z' } } } }],
      ['/feedback/analysis/', { data: { data: { id: 'insight_z' } } }],
      ['/feedback/history/list/', { data: { data: { analyses: [] } } }],
    ])
    const store = makeStore()
    const p = store.dispatch(resumeInFlightTask({ taskId: 'task-z', projectId: 'p1' }) as any)
    await vi.advanceTimersByTimeAsync(2000)
    const res: any = await p
    expect(res.type).toContain('/fulfilled')
    expect(res.payload.id).toBe('insight_z')
    expect(store.getState().analysis.selectedAnalysisId).toBe('insight_z')
  })
})
