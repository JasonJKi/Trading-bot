"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 10_000,
            refetchInterval: 15_000,
            refetchOnWindowFocus: false,
            retry: (failureCount, err) => {
              if ((err as { code?: number })?.code === 401) return false;
              return failureCount < 2;
            },
          },
        },
      })
  );
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
