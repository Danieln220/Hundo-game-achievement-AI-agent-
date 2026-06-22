import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Frontend dev server. The API base URL is read from VITE_API_URL at runtime
// (see src/api.ts); defaults to http://localhost:8000.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
});
