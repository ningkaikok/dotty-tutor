/// <reference types="vite/client" />

// TypeScript 7 validates side-effect imports more strictly. These declarations
// keep Vite-managed stylesheet imports type-safe without changing runtime code.
declare module "*.css";
