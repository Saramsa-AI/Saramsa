// Registers @testing-library/jest-dom matchers (toBeInTheDocument, etc.) on
// vitest's `expect` for type-checking. The runtime import lives in
// vitest.setup.ts, which tsconfig excludes from compilation, so the type
// augmentation is referenced here (this file is matched by the `**/*.ts` include).
import '@testing-library/jest-dom/vitest'
