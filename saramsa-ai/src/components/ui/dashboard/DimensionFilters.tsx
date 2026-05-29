'use client';

import { useState, useMemo } from 'react';
import { Filter, X, ChevronDown } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface DimensionFiltersProps {
  dimensions: Array<Record<string, any>>;
  onFilterChange?: (filters: Record<string, any>) => void;
}

// Compare two dimension values by their string form so a number `5` and the
// string "5" are treated as the same value regardless of which type the row
// (or the active filter) happens to use.
const sameValue = (a: any, b: any) => String(a) === String(b);

export function DimensionFilters({ dimensions, onFilterChange }: DimensionFiltersProps) {
  const [activeFilters, setActiveFilters] = useState<Record<string, any>>({});
  const [showDropdown, setShowDropdown] = useState(false);

  // Extract available filter dimensions and their unique values
  const availableFilters = useMemo(() => {
    if (!dimensions || dimensions.length === 0) return {};

    const filters: Record<string, Set<any>> = {};

    dimensions.forEach((dim) => {
      Object.entries(dim).forEach(([key, value]) => {
        if (value !== null && value !== undefined && value !== '') {
          if (!filters[key]) {
            filters[key] = new Set();
          }
          filters[key].add(value);
        }
      });
    });

    // Convert Sets to sorted arrays. Use a numeric sort when every value in the
    // dimension is numeric (so 1, 2, 10 — not the lexicographic 1, 10, 2);
    // otherwise fall back to a locale-aware string compare.
    const result: Record<string, any[]> = {};
    Object.entries(filters).forEach(([key, valueSet]) => {
      const values = Array.from(valueSet);
      const allNumeric = values.every(
        (v) => typeof v === 'number' || (v !== '' && Number.isFinite(Number(v)))
      );
      result[key] = allNumeric
        ? values.sort((a, b) => Number(a) - Number(b))
        : values.sort((a, b) => String(a).localeCompare(String(b)));
    });

    return result;
  }, [dimensions]);

  const filterCount = Object.keys(activeFilters).length;

  const handleFilterSelect = (dimension: string, value: any) => {
    const newFilters = { ...activeFilters };

    if (
      newFilters[dimension] !== undefined &&
      sameValue(newFilters[dimension], value)
    ) {
      // Remove filter if clicking the same value (compare type-insensitively
      // so a string "5" toggles a previously stored number 5 and vice versa)
      delete newFilters[dimension];
    } else {
      // Set new filter value
      newFilters[dimension] = value;
    }

    setActiveFilters(newFilters);
    onFilterChange?.(newFilters);
    setShowDropdown(false);
  };

  const handleClearFilters = () => {
    setActiveFilters({});
    onFilterChange?.({});
  };

  if (Object.keys(availableFilters).length === 0) {
    return null;
  }

  return (
    <div className="flex items-center gap-2">
      <div className="relative">
        <Button
          onClick={() => setShowDropdown(!showDropdown)}
          variant="outline"
          size="sm"
          className="flex items-center gap-2"
        >
          <Filter className="w-4 h-4" />
          Add Filter
          {filterCount > 0 && (
            <span className="ml-1 px-1.5 py-0.5 bg-saramsa-brand text-white text-xs rounded-full">
              {filterCount}
            </span>
          )}
          <ChevronDown className={`w-4 h-4 transition-transform ${showDropdown ? 'rotate-180' : ''}`} />
        </Button>

        {showDropdown && (
          <>
            <div
              className="fixed inset-0 z-10"
              onClick={() => setShowDropdown(false)}
            />
            <div className="absolute top-full left-0 mt-2 w-64 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg z-20 max-h-96 overflow-y-auto">
              {Object.entries(availableFilters).map(([dimension, values]) => (
                <div key={dimension} className="border-b border-gray-200 dark:border-gray-700 last:border-b-0">
                  <div className="px-3 py-2 bg-gray-50 dark:bg-gray-900 font-medium text-sm text-gray-700 dark:text-gray-300">
                    {dimension.replace(/_/g, ' ').replace(/\b\w/g, (l) => l.toUpperCase())}
                  </div>
                  <div className="py-1">
                    {values.map((value) => (
                      <button
                        key={String(value)}
                        onClick={() => handleFilterSelect(dimension, value)}
                        className={`w-full text-left px-3 py-2 text-sm hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors ${
                          activeFilters[dimension] !== undefined &&
                          sameValue(activeFilters[dimension], value)
                            ? 'bg-saramsa-brand/10 text-saramsa-brand font-medium'
                            : 'text-gray-700 dark:text-gray-300'
                        }`}
                      >
                        {String(value)}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </>
        )}
      </div>

      {filterCount > 0 && (
        <>
          <div className="flex items-center gap-2 flex-wrap">
            {Object.entries(activeFilters).map(([dimension, value]) => (
              <div
                key={dimension}
                className="flex items-center gap-1 px-2 py-1 bg-saramsa-brand/10 text-saramsa-brand border border-saramsa-brand/20 rounded-md text-sm"
              >
                <span className="font-medium">
                  {dimension.replace(/_/g, ' ').replace(/\b\w/g, (l) => l.toUpperCase())}:
                </span>
                <span>{String(value)}</span>
                <button
                  onClick={() => handleFilterSelect(dimension, value)}
                  className="ml-1 hover:bg-saramsa-brand/20 rounded-full p-0.5"
                >
                  <X className="w-3 h-3" />
                </button>
              </div>
            ))}
          </div>
          <Button
            onClick={handleClearFilters}
            variant="ghost"
            size="sm"
            className="text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
          >
            Clear All
          </Button>
        </>
      )}
    </div>
  );
}
