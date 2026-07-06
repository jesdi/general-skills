import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    include: ['tests/**/*.test.ts', 'cli/tests/**/*.test.ts'],
    passWithNoTests: true,
  },
});
