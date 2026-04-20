import * as esbuild from 'esbuild';
const config = {
  entryPoints: ['src/main.ts'], bundle: true, outputFile: 'main.js',
  external: ['obsidian', 'd3'], format: 'cjs', target: 'es2020', sourcemap: false, minify: true,
};
await esbuild.build(config);
console.log('Built successfully');