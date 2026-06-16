'use client';

import { useEffect, useState } from 'react';
import { Filter, X, ChevronDown } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Badge } from '@/components/ui/badge';
import { apiRequest } from '@/lib/apiRequest';

interface DimensionInfo {
  type: 'categorical' | 'numeric';
  values: (string | number)[];
  count: number;
  min?: number;
  max?: number;
}

interface DiscoveredDimensions {
  [key: string]: DimensionInfo;
}

interface DimensionFilter {
  key: string;
  operator: 'eq' | 'in' | 'gte' | 'lte';
  value: string | string[];
  label: string;
}

interface DynamicFilterBarProps {
  projectId: string;
  onFiltersChange?: (filters: DimensionFilter[]) => void;
  className?: string;
}

export function DynamicFilterBar({ projectId, onFiltersChange, className = '' }: DynamicFilterBarProps) {
  const [dimensions, setDimensions] = useState<DiscoveredDimensions>({});
  const [loading, setLoading] = useState(true);
  const [activeFilters, setActiveFilters] = useState<DimensionFilter[]>([]);
  const [showFilterPanel, setShowFilterPanel] = useState(false);

  // Fetch available dimensions on mount
  useEffect(() => {
    if (!projectId) return;

    const fetchDimensions = async () => {
      try {
        setLoading(true);
        const response = await apiRequest(
          'GET',
          `/feedback/projects/${projectId}/dimensions/`
        );
        setDimensions(response.data?.data?.dimensions || {});
      } catch (error) {
        console.error('Failed to fetch dimensions:', error);
      } finally {
        setLoading(false);
      }
    };

    fetchDimensions();
  }, [projectId]);

  // Notify the parent from inside the state-changing handlers (addFilter /
  // removeFilter / clearAllFilters) rather than from an effect keyed on the
  // filter list + `onFiltersChange`. Parents pass `onFiltersChange` as an
  // inline arrow (new identity every render); an effect depending on it would
  // re-fire on every parent render and trigger redundant filtered-analysis
  // fetches / a render loop. `applyFilters` derives `next` from the current
  // committed state (the closure value, correct for these one-shot click
  // handlers), sets it, then emits it — keeping the setState updater pure so
  // it stays safe under React StrictMode's double-invocation.
  const applyFilters = (
    updater: (prev: DimensionFilter[]) => DimensionFilter[]
  ) => {
    const next = updater(activeFilters);
    setActiveFilters(next);
    onFiltersChange?.(next);
  };

  const addFilter = (key: string, operator: 'eq' | 'in' | 'gte' | 'lte', value: string | string[]) => {
    const label = formatDimensionLabel(key);
    const newFilter: DimensionFilter = { key, operator, value, label };

    // Replace existing filter for same key or add new
    applyFilters(prev => {
      const existing = prev.findIndex(f => f.key === key);
      if (existing >= 0) {
        const updated = [...prev];
        updated[existing] = newFilter;
        return updated;
      }
      return [...prev, newFilter];
    });
  };

  const removeFilter = (key: string) => {
    applyFilters(prev => prev.filter(f => f.key !== key));
  };

  const clearAllFilters = () => {
    applyFilters(() => []);
  };

  const formatDimensionLabel = (key: string): string => {
    // Convert snake_case to Title Case
    return key
      .split('_')
      .map(word => word.charAt(0).toUpperCase() + word.slice(1))
      .join(' ');
  };

  const formatFilterDisplay = (filter: DimensionFilter): string => {
    const { operator, value } = filter;

    if (operator === 'eq') {
      return `${filter.label}: ${value}`;
    } else if (operator === 'in' && Array.isArray(value)) {
      return `${filter.label}: ${value.join(', ')}`;
    } else if (operator === 'gte') {
      return `${filter.label} ≥ ${value}`;
    } else if (operator === 'lte') {
      return `${filter.label} ≤ ${value}`;
    }
    return `${filter.label}: ${value}`;
  };

  const dimensionKeys = Object.keys(dimensions);

  if (loading) {
    return (
      <div className={`flex items-center gap-2 text-sm text-muted-foreground ${className}`}>
        <Filter className="h-4 w-4 animate-pulse" />
        <span>Loading filters...</span>
      </div>
    );
  }

  if (dimensionKeys.length === 0) {
    return null; // No dimensions available
  }

  return (
    <div className={`space-y-3 ${className}`}>
      {/* Filter Controls */}
      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={() => setShowFilterPanel(!showFilterPanel)}
          className="gap-2"
        >
          <Filter className="h-4 w-4" />
          Add Filter
          <ChevronDown className={`h-4 w-4 transition-transform ${showFilterPanel ? 'rotate-180' : ''}`} />
        </Button>

        {activeFilters.length > 0 && (
          <Button
            variant="ghost"
            size="sm"
            onClick={clearAllFilters}
            className="text-muted-foreground"
          >
            Clear all
          </Button>
        )}
      </div>

      {/* Active Filters */}
      {activeFilters.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {activeFilters.map((filter) => (
            <Badge
              key={filter.key}
              variant="secondary"
              className="gap-1 pl-2 pr-1 py-1"
            >
              <span className="text-xs">{formatFilterDisplay(filter)}</span>
              <Button
                variant="ghost"
                size="icon"
                className="h-4 w-4 p-0 hover:bg-transparent"
                onClick={() => removeFilter(filter.key)}
              >
                <X className="h-3 w-3" />
              </Button>
            </Badge>
          ))}
        </div>
      )}

      {/* Filter Selection Panel */}
      {showFilterPanel && (
        <div className="border rounded-lg p-4 space-y-4 bg-muted/50">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {dimensionKeys.map((key) => {
              const dimension = dimensions[key];
              const activeFilter = activeFilters.find(f => f.key === key);

              return (
                <DimensionFilterControl
                  key={key}
                  dimensionKey={key}
                  dimension={dimension}
                  activeValue={activeFilter}
                  onFilterChange={(operator, value) => addFilter(key, operator, value)}
                  onFilterRemove={() => removeFilter(key)}
                />
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

interface DimensionFilterControlProps {
  dimensionKey: string;
  dimension: DimensionInfo;
  activeValue?: DimensionFilter;
  onFilterChange: (operator: 'eq' | 'in' | 'gte' | 'lte', value: string | string[]) => void;
  onFilterRemove: () => void;
}

function DimensionFilterControl({
  dimensionKey,
  dimension,
  activeValue,
  onFilterChange,
  onFilterRemove,
}: DimensionFilterControlProps) {
  const [selectedOperator, setSelectedOperator] = useState<'eq' | 'in' | 'gte' | 'lte'>(
    activeValue?.operator || (dimension.type === 'numeric' ? 'gte' : 'eq')
  );
  const [selectedValue, setSelectedValue] = useState<string>(
    activeValue ? (Array.isArray(activeValue.value) ? activeValue.value[0] : String(activeValue.value)) : ''
  );
  const [selectedValues, setSelectedValues] = useState<string[]>(
    activeValue && Array.isArray(activeValue.value) ? activeValue.value : []
  );

  const label = dimensionKey
    .split('_')
    .map(word => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');

  const handleApply = () => {
    if (selectedOperator === 'in') {
      if (selectedValues.length > 0) {
        onFilterChange(selectedOperator, selectedValues);
      }
    } else if (selectedValue) {
      onFilterChange(selectedOperator, selectedValue);
    }
  };

  const toggleValue = (value: string) => {
    setSelectedValues(prev =>
      prev.includes(value)
        ? prev.filter(v => v !== value)
        : [...prev, value]
    );
  };

  return (
    <div className="space-y-2">
      <label className="text-sm font-medium">{label}</label>

      {/* Operator selector for numeric dimensions */}
      {dimension.type === 'numeric' && (
        <select
          value={selectedOperator}
          onChange={(e) => setSelectedOperator(e.target.value as any)}
          className="flex h-10 w-full items-center justify-between rounded-md border border-border bg-background px-3 py-2 text-sm"
        >
          <option value="gte">Greater than or equal</option>
          <option value="lte">Less than or equal</option>
          <option value="eq">Equals</option>
        </select>
      )}

      {/* Categorical: single or multiple select */}
      {dimension.type === 'categorical' && selectedOperator !== 'in' && (
        <select
          value={selectedValue}
          onChange={(e) => setSelectedValue(e.target.value)}
          className="flex h-10 w-full items-center justify-between rounded-md border border-border bg-background px-3 py-2 text-sm"
        >
          <option value="">Select {label.toLowerCase()}</option>
          {dimension.values.map((value) => (
            <option key={String(value)} value={String(value)}>
              {String(value)}
            </option>
          ))}
        </select>
      )}

      {/* Categorical: multi-select mode */}
      {dimension.type === 'categorical' && selectedOperator === 'in' && (
        <div className="border rounded-md p-2 max-h-32 overflow-y-auto space-y-1">
          {dimension.values.map((value) => (
            <label
              key={String(value)}
              className="flex items-center gap-2 cursor-pointer hover:bg-muted px-2 py-1 rounded"
            >
              <input
                type="checkbox"
                checked={selectedValues.includes(String(value))}
                onChange={() => toggleValue(String(value))}
                className="rounded"
              />
              <span className="text-sm">{String(value)}</span>
            </label>
          ))}
        </div>
      )}

      {/* Numeric: value input */}
      {dimension.type === 'numeric' && (
        <input
          type="number"
          value={selectedValue}
          onChange={(e) => setSelectedValue(e.target.value)}
          placeholder={`Enter value (${dimension.min} - ${dimension.max})`}
          className="w-full px-3 py-2 border rounded-md text-sm"
          min={dimension.min}
          max={dimension.max}
        />
      )}

      {/* Apply/Remove buttons */}
      <div className="flex gap-2">
        <Button
          size="sm"
          onClick={handleApply}
          disabled={
            (selectedOperator === 'in' && selectedValues.length === 0) ||
            (selectedOperator !== 'in' && !selectedValue)
          }
          className="flex-1"
        >
          Apply
        </Button>
        {activeValue && (
          <Button
            size="sm"
            variant="ghost"
            onClick={onFilterRemove}
          >
            Clear
          </Button>
        )}
      </div>
    </div>
  );
}
