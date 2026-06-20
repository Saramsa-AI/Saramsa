import { describe, expect, it } from 'vitest';
import {
  isAnalyzingPlaceholder,
  isSyntheticAnalysisId,
  isFetchableAnalysisId,
  makeAnalyzingId,
} from './analysisConstants';

describe('analysis id classification', () => {
  describe('isSyntheticAnalysisId', () => {
    it('flags the client-only analysis_<ts> fallback', () => {
      expect(isSyntheticAnalysisId('analysis_1781572317081')).toBe(true);
    });
    it('does not flag a real insight id', () => {
      expect(isSyntheticAnalysisId('insight_7ca2a98b-5c69-4954-be56-9321f0deed4e')).toBe(false);
    });
    it('does not flag the backend analysis_<uuid> alias (only the digit timestamp form)', () => {
      // The backend rewrites analysis_<uuid> -> insight_<uuid>; it is a real,
      // fetchable id and must NOT be treated as synthetic.
      expect(isSyntheticAnalysisId('analysis_7ca2a98b-5c69-4954-be56-9321f0deed4e')).toBe(false);
    });
    it('is safe on null/undefined', () => {
      expect(isSyntheticAnalysisId(null)).toBe(false);
      expect(isSyntheticAnalysisId(undefined)).toBe(false);
    });
  });

  describe('isFetchableAnalysisId', () => {
    it('accepts a real insight id', () => {
      expect(isFetchableAnalysisId('insight_7ca2a98b-5c69-4954-be56-9321f0deed4e')).toBe(true);
    });
    it('accepts a bare uuid analysis id', () => {
      expect(isFetchableAnalysisId('7ca2a98b-5c69-4954-be56-9321f0deed4e')).toBe(true);
    });
    it('rejects the synthetic analysis_<ts> fallback (the run-shows-as-failed bug)', () => {
      expect(isFetchableAnalysisId('analysis_1781572317081')).toBe(false);
    });
    it('accepts the backend analysis_<uuid> alias', () => {
      expect(isFetchableAnalysisId('analysis_7ca2a98b-5c69-4954-be56-9321f0deed4e')).toBe(true);
    });
    it('rejects the in-flight analyzing_ placeholder', () => {
      expect(isFetchableAnalysisId(makeAnalyzingId('fbfee090'))).toBe(false);
    });
    it('rejects null, undefined, and empty/whitespace', () => {
      expect(isFetchableAnalysisId(null)).toBe(false);
      expect(isFetchableAnalysisId(undefined)).toBe(false);
      expect(isFetchableAnalysisId('')).toBe(false);
      expect(isFetchableAnalysisId('   ')).toBe(false);
    });
  });

  describe('isAnalyzingPlaceholder', () => {
    it('round-trips with makeAnalyzingId', () => {
      expect(isAnalyzingPlaceholder(makeAnalyzingId('abc'))).toBe(true);
      expect(isAnalyzingPlaceholder('analysis_123')).toBe(false);
    });
  });
});
