import { describe, test, expect, vi, beforeEach } from 'vitest'

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
