module.exports = {
  root: true,
  env: { browser: true, es2020: true },
  extends: [
    'eslint:recommended',
    'plugin:@typescript-eslint/recommended',
    'plugin:react-hooks/recommended',
  ],
  ignorePatterns: ['dist', '.eslintrc.cjs'],
  parser: '@typescript-eslint/parser',
  plugins: ['react-refresh'],
  rules: {
    'react-refresh/only-export-components': [
      'warn',
      { allowConstantExport: true },
    ],
    '@typescript-eslint/no-explicit-any': 'warn',
    '@typescript-eslint/no-unused-vars': ['warn', { argsIgnorePattern: '^_' }],
    // Allow while(true) / for(;;) retry loops in SSE/stream code
    'no-constant-condition': ['error', { checkLoops: false }],
    // Allow empty catch blocks for intentional error suppression
    'no-empty': ['error', { allowEmptyCatch: true }],
    // Allow escape characters in regex (common in string patterns)
    'no-useless-escape': 'warn',
  },
};
