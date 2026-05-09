import { defineConfig, loadEnv } from "vite";
import vue from "@vitejs/plugin-vue";
import { fileURLToPath, URL } from "node:url";

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "VITE_");

  return {
    plugins: [vue()],
    resolve: {
      alias: {
        "@": fileURLToPath(new URL("./src", import.meta.url)),
      },
    },
    server: {
      host: "0.0.0.0",
      port: 5173,
      strictPort: true,
      proxy: {
        // Dev 模式下，代理 /api 到後端，避免 CORS（OWASP A05 — prod 走 nginx）
        "/api": {
          target: env.VITE_API_BASE_URL ?? "http://localhost:8000",
          changeOrigin: true,
          secure: false,
        },
      },
    },
    build: {
      target: "es2022",
      sourcemap: false, // prod 不出 sourcemap（避免洩漏內部資訊）
      rollupOptions: {
        output: {
          // 更佳的 chunk 切割
          manualChunks: {
            "naive-ui": ["naive-ui"],
            "vue-ecosystem": ["vue", "vue-router", "pinia"],
          },
        },
      },
    },
  };
});
