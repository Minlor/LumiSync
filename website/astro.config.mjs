import { defineConfig } from "astro/config";
import react from "@astrojs/react";

export default defineConfig({
  site: "https://lumisync.minlor.net",
  output: "static",
  integrations: [react()],
  vite: {
    ssr: {
      noExternal: ["lucide-react", "react-icons"],
    },
  },
});
