const API_BASE = process.env.NEXT_PUBLIC_KATRACE_API_URL || "";

export async function fetchJson<T>(path: string, timeoutMs = 15000): Promise<T> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      signal: controller.signal,
      cache: "no-store",
    });
    const text = await response.text();
    if (!response.ok) {
      throw new Error(`${path} returned HTTP ${response.status}: ${text.slice(0, 300)}`);
    }
    try {
      return JSON.parse(text) as T;
    } catch {
      throw new Error(`${path} returned non-JSON content: ${text.slice(0, 300)}`);
    }
  } catch (error: any) {
    if (error?.name === "AbortError") {
      throw new Error(`${path} timed out after ${timeoutMs / 1000} seconds.`);
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}
