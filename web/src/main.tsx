import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { ToastProvider } from "./components/toast";
import { MotionRoot } from "./motion";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchInterval: 15_000, staleTime: 5_000 } },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <MotionRoot>
          <ToastProvider>
            <App />
          </ToastProvider>
        </MotionRoot>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
