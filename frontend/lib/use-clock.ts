"use client";
import { useEffect, useState } from "react";

/** Local wall-clock as HH:MM:SS. Rendered only after mount so server and client
 * markup never disagree on the time string. */
export function useLocalClock(intervalMs = 1000): string {
  const [time, setTime] = useState("--:--:--");

  useEffect(() => {
    const tick = () =>
      setTime(
        new Date().toLocaleTimeString([], {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
          hour12: false,
        }),
      );
    tick();
    const id = setInterval(tick, intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);

  return time;
}
