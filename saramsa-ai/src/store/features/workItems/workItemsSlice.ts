import { createSlice, PayloadAction } from '@reduxjs/toolkit';

export interface ActionItem {
  id: string;
  title: string;
  description: string;
  priority: 'low' | 'medium' | 'high' | 'critical';
  type: 'feature' | 'bug' | 'change';
  status: 'todo' | 'in_progress' | 'done';
  assignee?: string;
  dueDate?: string;
  createdAt: string;
  updatedAt: string;
  tags?: string[];
  acceptance?: string;
  isCompleted?: boolean;
  featureId?: string;
  featureArea?: string;
  submitted?: boolean;
  submittedAt?: string;
  review_status?: 'pending' | 'approved' | 'dismissed' | 'snoozed' | 'merged';
  push_status?: 'not_pushed' | 'pushed' | 'failed';
}

export interface Feature {
  id: string;
  name: string;
  title: string;
  description: string;
  priority: 'low' | 'medium' | 'high' | 'critical';
  status: 'proposed' | 'approved' | 'in_development' | 'testing' | 'released';
  assignee?: string;
  dueDate?: string;
  createdAt: string;
  updatedAt: string;
  color: string;
  actions: ActionItem[];
}

interface WorkItemsState {
  actionItems: ActionItem[];
  features: Feature[];
  selectedActions: string[];
  loading: boolean;
  error: string | null;
  selectedItem: ActionItem | Feature | null;
}

const initialState: WorkItemsState = {
  actionItems: [],
  features: [],
  selectedActions: [],
  loading: false,
  error: null,
  selectedItem: null,
};

const workItemsSlice = createSlice({
  name: 'workItems',
  initialState,
  reducers: {
    addActionItem: (state, action: PayloadAction<ActionItem>) => {
      state.actionItems.push(action.payload);
    },
    updateActionItem: (state, action: PayloadAction<ActionItem>) => {
      const index = state.actionItems.findIndex(item => item.id === action.payload.id);
      if (index !== -1) {
        state.actionItems[index] = action.payload;
      }
    },
    removeActionItem: (state, action: PayloadAction<string>) => {
      state.actionItems = state.actionItems.filter(item => item.id !== action.payload);
    },
    addFeature: (state, action: PayloadAction<Feature>) => {
      state.features.push(action.payload);
    },
    updateFeature: (state, action: PayloadAction<Feature>) => {
      const index = state.features.findIndex(item => item.id === action.payload.id);
      if (index !== -1) {
        state.features[index] = action.payload;
      }
    },
    removeFeature: (state, action: PayloadAction<string>) => {
      state.features = state.features.filter(item => item.id !== action.payload);
    },
    toggleActionSelection: (state, action: PayloadAction<string>) => {
      const actionId = action.payload;
      if (state.selectedActions.includes(actionId)) {
        state.selectedActions = state.selectedActions.filter(id => id !== actionId);
      } else {
        state.selectedActions.push(actionId);
      }
    },
    clearSelectedActions: (state) => {
      state.selectedActions = [];
    },
    clearActionItems: (state) => {
      state.actionItems = [];
    },
    setSelectedItem: (state, action: PayloadAction<ActionItem | Feature | null>) => {
      state.selectedItem = action.payload;
    },
    clearError: (state) => {
      state.error = null;
    },
  },
});

export const {
  addActionItem,
  updateActionItem,
  removeActionItem,
  addFeature,
  updateFeature,
  removeFeature,
  toggleActionSelection,
  clearSelectedActions,
  clearActionItems,
  setSelectedItem,
  clearError,
} = workItemsSlice.actions;

export default workItemsSlice.reducer;
