import { defineConfig, globalIgnores } from 'eslint/config';
import nextVitals from 'eslint-config-next/core-web-vitals';
import nextTs from 'eslint-config-next/typescript';

export default defineConfig([
  ...nextVitals,
  ...nextTs,
  globalIgnores([
    '.next/**',
    '.venv/**',
    '.vinext/**',
    '.wrangler/**',
    'dist/**',
    'htmlcov/**',
    'next-env.d.ts',
  ]),
]);
