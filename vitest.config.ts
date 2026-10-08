import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    include: ['tests/**/*.test.ts', 'cli/tests/**/*.test.ts'],
    passWithNoTests: true,
    coverage: {
      provider: 'istanbul',
      all: true,
      include: ['cli/src/**/*.ts', 'lib/**/*.ts', 'scripts/**/*.ts'],
      exclude: ['**/*.test.ts', '**/*.d.ts'],
      reportsDirectory: '.crap/typescript',
      reporter: ['text', 'json'],
    },
  },
});
