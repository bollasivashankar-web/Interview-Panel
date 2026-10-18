export function apiErrorMessage(body: unknown): string {
  const detail = body && typeof body === "object" && "detail" in body ? body.detail : undefined;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const messages = detail.flatMap((item: unknown) => {
      if (!item || typeof item !== "object" || !("msg" in item) || typeof item.msg !== "string") return [];
      const field = "loc" in item && Array.isArray(item.loc) ? item.loc.filter(part => part !== "body").join(".") : "";
      return [field ? `${field}: ${item.msg}` : item.msg];
    });
    if (messages.length) return messages.join("; ");
  }
  return "Request failed. Please try again.";
}
